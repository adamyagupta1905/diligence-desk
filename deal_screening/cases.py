"""Application service: durable graph sessions, immutable revisions and stale-tab checks."""
from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from .approvals import ApprovalPolicy
from .documents import DocumentReader
from .storage import CaseRepository, StaleRevisionError


@dataclass(frozen=True)
class RevisionView:
    revision: dict
    state: dict
    next: tuple
    checkpoint_id: str
    is_current: bool
    status: str
    interrupted: bool


class CaseService:
    def __init__(self, repository: CaseRepository, workflow_factory):
        self.repository = repository
        self.workflow_factory = workflow_factory

    @contextmanager
    def graph(self):
        conn = sqlite3.connect(self.repository.checkpoint_path, check_same_thread=False, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            saver = SqliteSaver(conn, serde=JsonPlusSerializer(
                pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[]))
            saver.setup()
            self.repository.checkpoint_path.chmod(0o600)
            yield self.workflow_factory().build(checkpointer=saver)
        finally:
            conn.close()

    @staticmethod
    def config(revision_id):
        return {"configurable": {"thread_id": revision_id}}

    def view(self, revision_id):
        revision = self.repository.revision(revision_id)
        with self.graph() as graph:
            snapshot = graph.get_state(self.config(revision_id))
        current = self.repository.case(revision["case_id"])["active_revision_id"] == revision_id
        state = snapshot.values or {}
        checkpoint = snapshot.config.get("configurable", {}).get("checkpoint_id", "")
        interrupted = any(task.interrupts for task in snapshot.tasks)
        return RevisionView(revision, state, snapshot.next, checkpoint, current,
                            ApprovalPolicy.status(state, current), interrupted)

    def create(self, title, source, name, model, author, reason, *, force_ocr=False, ledger_enabled=False):
        pages = DocumentReader.read(source, name, force_ocr=force_ocr)
        revision = self.repository.create(title, source, name, pages, model, author, reason, ledger_enabled=ledger_enabled)
        with self.repository.lock(revision["case_id"]):
            self._run_initial(revision)
        return revision["id"]

    def _run_initial(self, revision):
        with self.graph() as graph:
            graph.invoke(revision["initial_state"], self.config(revision["id"]))

    @staticmethod
    def _require_fresh(view, expected_checkpoint):
        if not view.is_current:
            raise StaleRevisionError("This revision was superseded. Open the current revision before reviewing.")
        if view.checkpoint_id != expected_checkpoint:
            raise StaleRevisionError("This review page is out of date. Refresh and inspect the latest results.")

    def resume(self, revision_id, answer, expected_checkpoint):
        revision = self.repository.revision(revision_id)
        with self.repository.lock(revision["case_id"]):
            view = self.view(revision_id)
            self._require_fresh(view, expected_checkpoint)
            if not view.interrupted or not set(view.next) & {"input_review", "memo_review", "ledger_review"}:
                raise ValueError("This revision is not waiting for a review decision.")
            # Invalid answers must not be persisted as an interrupt's cached
            # resume value; otherwise a corrected resubmission can replay the
            # original invalid answer. Preflight is deterministic and API-free.
            workflow=self.workflow_factory()
            if "ledger_review" in view.next:
                workflow.ledger.prepare_review(view.state,answer)
            elif "input_review" in view.next:
                workflow.prepare_input_review(view.state,answer)
            elif "memo_review" in view.next:
                workflow.prepare_memo_review(view.state,answer)
            with self.graph() as graph:
                graph.invoke(Command(resume=answer), self.config(revision_id))
        return self.view(revision_id)

    def revise(self, revision_id, author, reason, expected_checkpoint,
               source=None, name=None, *, force_ocr=False, ledger_enabled=None, reextract_ledger=False):
        parent = self.repository.revision(revision_id)
        replacing_document = source is not None
        old_ledger=bool(parent["initial_state"].get("ledger_enabled"))
        enabled=old_ledger if ledger_enabled is None else bool(ledger_enabled)
        # Validate the upload before invalidating an approval or changing the head.
        if source is not None:
            if not name:
                raise ValueError("A document filename is required.")
            if source == parent["source"] and not force_ocr:
                raise ValueError("This document has identical bytes. No new document revision was created.")
            pages = DocumentReader.read(source, name, force_ocr=force_ocr)
        else:
            source, name = parent["source"], parent["document_name"]
            pages = parent["initial_state"]["pages"]
        kind = ("ocr" if force_ocr else "document") if replacing_document else ("ledger" if reextract_ledger or enabled != old_ledger else "inputs")
        with self.repository.lock(parent["case_id"]):
            view = self.view(revision_id)
            self._require_fresh(view, expected_checkpoint)
            if kind == "inputs" and not view.state.get("facts") and not view.state.get("ledger_rows"):
                raise ValueError("Process the current document before revising its inputs.")
            keep_history=enabled and old_ledger and not replacing_document and not reextract_ledger and bool(view.state.get("ledger_context")) and "ledger_raw" in view.state
            seed_ledger={key:view.state[key] for key in (
                "ledger_context","ledger_context_draft","ledger_raw","ledger_rows","ledger_errors","ledger_retrieval",
                "ledger_selected_year") if key in view.state} if keep_history else None
            keep_facts=not replacing_document and not (old_ledger and not enabled)
            revision = self.repository.branch(
                parent["case_id"], revision_id, source, name, pages,
                view.state.get("model_name", parent["initial_state"]["model_name"]),
                author, reason, kind, view.status,
                seed_facts=view.state.get("facts") if keep_facts else None,
                seed_assumptions=view.state.get("assumptions") if keep_facts else None,
                ledger_enabled=enabled,seed_ledger=seed_ledger)
            self._run_initial(revision)
        return revision["id"]

    def replace_document(self, revision_id, source, name, author, reason, expected_checkpoint, *, force_ocr=False, ledger_enabled=None):
        parent = self.repository.revision(revision_id)
        if source == parent["source"] and not force_ocr:
            raise ValueError("This document has identical bytes. No new document revision was created.")
        return self.revise(revision_id, author, reason, expected_checkpoint,source,name,force_ocr=force_ocr,ledger_enabled=ledger_enabled)

    def retry_processing(self, revision_id, expected_checkpoint):
        revision = self.repository.revision(revision_id)
        with self.repository.lock(revision["case_id"]):
            view = self.view(revision_id)
            self._require_fresh(view, expected_checkpoint)
            if view.interrupted:
                raise ValueError("Use the review form to continue this revision.")
            if view.state and not view.next:
                raise ValueError("This revision has completed. Create a revision to make changes.")
            with self.graph() as graph:
                graph.invoke(None if view.state else revision["initial_state"], self.config(revision_id))
        return self.view(revision_id)
