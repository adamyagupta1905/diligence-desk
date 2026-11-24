"""SQLite case metadata and immutable source revisions; model keys are never stored."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from filelock import FileLock, Timeout


class StaleRevisionError(ValueError):
    pass


class CaseBusyError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class CaseRepository:
    SCHEMA_VERSION = 1

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "cases.sqlite3"
        self.checkpoint_path = self.directory / "checkpoints.sqlite3"
        self.lock_dir = self.directory / "locks"
        self.lock_dir.mkdir(exist_ok=True, mode=0o700)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.SCHEMA_VERSION):
                raise ValueError("Unsupported case database version; do not overwrite this database.")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    active_revision_id TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    number INTEGER NOT NULL, parent_id TEXT REFERENCES revisions(id),
                    kind TEXT NOT NULL, document_name TEXT NOT NULL,
                    document_hash TEXT NOT NULL, source BLOB NOT NULL,
                    initial_state TEXT NOT NULL, author TEXT NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(case_id, number));
                CREATE TABLE IF NOT EXISTS case_events (
                    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            conn.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def lock(self, case_id):
        if not re.fullmatch(r"[0-9a-f]{32}", case_id):
            raise ValueError("Invalid case identifier.")
        # The OS releases this lock after a crash; no stale lease to approve through.
        try:
            with FileLock(self.lock_dir / f"{case_id}.lock", timeout=0):
                yield
        except Timeout as exc:
            raise CaseBusyError("This case is being processed in another tab. Wait, then refresh.") from exc

    def list_cases(self):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM cases ORDER BY created_at DESC, id")]

    def case(self, case_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("Case not found.")
        return dict(row)

    def revision(self, revision_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
        if row is None:
            raise ValueError("Revision not found.")
        result = dict(row)
        result["initial_state"] = json.loads(result["initial_state"])
        return result

    def revisions(self, case_id):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id,case_id,number,parent_id,kind,document_name,document_hash,author,reason,created_at "
                "FROM revisions WHERE case_id=? ORDER BY number DESC", (case_id,))]

    def events(self, case_id):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM case_events WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
        return [{**dict(row), "details": json.loads(row["details"])} for row in rows]

    def create(self, title, source, name, pages, model, author, reason, *, ledger_enabled=False):
        self._require_review(author, reason)
        if not title.strip():
            raise ValueError("Enter a case title.")
        case_id, revision_id = uuid.uuid4().hex, uuid.uuid4().hex
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO cases VALUES (?,?,NULL,?)", (case_id, title.strip(), now()))
            self._insert(conn, case_id, revision_id, 1, None, "initial", source, name,
                         pages, model, author, reason, ledger_enabled=ledger_enabled)
        return self.revision(revision_id)

    def branch(self, case_id, expected_parent, source, name, pages, model, author,
               reason, kind, prior_status, seed_facts=None, seed_assumptions=None,
               ledger_enabled=False, seed_ledger=None):
        self._require_review(author, reason)
        if kind not in {"document", "inputs", "ocr", "ledger"}:
            raise ValueError("Unknown revision type.")
        revision_id = uuid.uuid4().hex
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if not case or case["active_revision_id"] != expected_parent:
                raise StaleRevisionError("The current revision changed. Refresh before creating another revision.")
            parent = conn.execute("SELECT * FROM revisions WHERE id=? AND case_id=?",
                                  (expected_parent, case_id)).fetchone()
            digest = hashlib.sha256(source).hexdigest()
            if kind == "document" and digest == parent["document_hash"]:
                raise ValueError("This document has identical bytes. No new document revision was created.")
            self._insert(conn, case_id, revision_id, parent["number"] + 1, expected_parent,
                         kind, source, name, pages, model, author, reason,
                         seed_facts, seed_assumptions, prior_status, ledger_enabled, seed_ledger)
        return self.revision(revision_id)

    @staticmethod
    def _require_review(author, reason):
        if not author.strip() or not reason.strip():
            raise ValueError("Enter your name and a reason for this case/revision.")

    def _insert(self, conn, case_id, revision_id, number, parent_id, kind, source,
                name, pages, model, author, reason, seed_facts=None, seed_assumptions=None,
                prior_status=None, ledger_enabled=False, seed_ledger=None):
        created = now()
        digest = hashlib.sha256(source).hexdigest()
        details = {"revision_id": revision_id, "parent_revision_id": parent_id,
                   "author": author.strip(), "reason": reason.strip(),
                   "kind": kind, "prior_status": prior_status}
        audit = [{"time_utc": created, "action": "revision_created", **details}]
        if parent_id:
            audit.append({"time_utc": created, "action": "prior_review_not_carried_forward",
                          "parent_revision_id": parent_id, "prior_status": prior_status,
                          "reason": "New revision requires fresh input and memo review."})
        initial = {"case_id": case_id, "revision_id": revision_id, "revision_number": number,
                   "document_name": name, "document_hash": digest, "pages": pages,
                   "model_name": model, "audit": audit, "feedback": "", "decision": "pending",
                   "approved_fingerprint": "", "reuse_facts": seed_facts is not None,
                   "ledger_enabled":bool(ledger_enabled),"reuse_ledger":seed_ledger is not None}
        if seed_ledger is not None:
            initial.update(seed_ledger)
            initial["ledger_reviewed"] = False
        if seed_facts is not None:
            initial.update(facts=seed_facts, assumptions=seed_assumptions or {}, extraction_error="")
        conn.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (revision_id, case_id, number, parent_id, kind, name, digest, source,
                      json.dumps(initial, ensure_ascii=False, allow_nan=False),
                      author.strip(), reason.strip(), created))
        conn.execute("UPDATE cases SET active_revision_id=? WHERE id=?", (revision_id, case_id))
        conn.execute("INSERT INTO case_events(case_id,revision_id,action,details,created_at) VALUES (?,?,?,?,?)",
                     (case_id, revision_id, "revision_created", json.dumps(details), created))
