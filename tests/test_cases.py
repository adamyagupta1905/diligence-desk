"""Durability, version controls, stale submissions and deterministic change review."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening.approvals import ApprovalPolicy
from deal_screening.cases import CaseService
from deal_screening.comparison import DocumentComparator
from deal_screening.documents import DocumentReader
from deal_screening.reporting import report_text
from deal_screening.storage import CaseRepository, CaseBusyError, StaleRevisionError
from deal_screening.workflow import ScreeningWorkflow
from helpers import fixture, FakeExtractor, FakeMemoWriter, ASSUMPTIONS, ROOT


class CaseServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pages, self.chunks, self.facts = fixture()
        self.source = (ROOT / "sample_documents" / "01_complete_millions.txt").read_bytes()
        self.service = self.reopen()
        self.rid = self.service.create("Northstar", self.source, "original.txt", "offline",
                                       "Test analyst", "Initial test")

    def reopen(self, facts=None):
        return CaseService(CaseRepository(self.directory.name), lambda: ScreeningWorkflow(
            FakeExtractor(self.facts if facts is None else facts), FakeMemoWriter()))

    def confirm(self, rid=None, assumptions=None, service=None):
        service = service or self.service
        rid = rid or self.rid
        view = service.view(rid)
        return service.resume(rid, {"facts": view.state["facts"],
            "assumptions": assumptions or ASSUMPTIONS, "reviewer": "Test", "note": "Checked evidence"},
            view.checkpoint_id)

    def approve(self):
        view = self.confirm()
        return self.service.resume(self.rid, {"decision": "approved", "reviewer": "Test",
                                             "note": "Checked memo"}, view.checkpoint_id)

    def test_input_and_memo_checkpoints_survive_new_service_instances(self):
        reopened = self.reopen()
        view = reopened.view(self.rid)
        self.assertEqual(view.next, ("input_review",))
        self.assertTrue(view.interrupted)
        self.confirm(service=reopened)
        again = self.reopen()
        view = again.view(self.rid)
        self.assertEqual(view.next, ("memo_review",))
        approved = again.resume(self.rid, {"decision": "approved", "reviewer": "Test", "note": "Checked"}, view.checkpoint_id)
        self.assertEqual(approved.status, "approved")
        self.assertEqual(self.reopen().view(self.rid).status, "approved")

    def test_saved_approval_can_be_read_in_a_separate_python_process(self):
        self.approve()
        script = '''
import json, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[1] + "/tests")
from deal_screening.cases import CaseService
from deal_screening.storage import CaseRepository
from deal_screening.workflow import ScreeningWorkflow
from helpers import fixture, FakeExtractor, FakeMemoWriter
service = CaseService(CaseRepository(sys.argv[2]), lambda: ScreeningWorkflow(FakeExtractor(fixture()[2]), FakeMemoWriter()))
view = service.view(sys.argv[3])
print(json.dumps({"status": view.status, "equity": view.state["calculations"]["equity_value"]}))
'''
        result = subprocess.run([sys.executable, "-c", script, str(ROOT), self.directory.name, self.rid],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "approved", "equity": 68_000_000})

    def test_new_input_revision_invalidates_current_approval_without_erasing_history(self):
        old = self.approve()
        child = self.service.revise(self.rid, "Test", "Change multiple", old.checkpoint_id)
        new = self.service.view(child)
        self.assertEqual(new.status, "pending")
        self.assertEqual(new.next, ("input_review",))
        self.assertNotIn("calculations", new.state)
        self.assertNotIn("memo", new.state)
        self.assertEqual(new.state["approved_fingerprint"], "")
        self.assertEqual(new.state["assumptions"], ASSUMPTIONS)
        historical = self.service.view(self.rid)
        self.assertEqual(historical.status, "superseded")
        self.assertEqual(historical.state["decision"], "approved")
        self.assertEqual(historical.state["approved_fingerprint"], old.state["approved_fingerprint"])
        self.assertIn("Status: superseded", report_text(historical.state, current_revision_id=child))
        changed = self.confirm(child, {**ASSUMPTIONS, "multiple": 9.})
        self.assertEqual(changed.state["calculations"]["equity_value"], 60_000_000)
        self.assertEqual(changed.status, "pending")
        final = self.service.resume(child, {"decision": "approved", "reviewer": "Test", "note": "Reviewed v2"}, changed.checkpoint_id)
        self.assertEqual(final.status, "approved")
        self.assertNotEqual(final.state["approved_fingerprint"], old.state["approved_fingerprint"])

    def test_document_replacement_reextracts_and_does_not_copy_reviewed_facts(self):
        old = self.approve()
        changed_source = (ROOT / "sample_documents" / "10_version_revised.txt").read_bytes()
        changed_facts = deepcopy(self.facts)
        # Distinct fake extraction verifies the new extraction path actually runs.
        new_values = {"revenue": "45000000", "ebitda": "7000000", "net_debt": "15000000",
                      "revenue_growth": "0.08", "customer_concentration": "0.55"}
        new_chunks = DocumentReader.chunks(DocumentReader.read(changed_source, "revised.txt"))
        for fact in changed_facts:
            fact["value"] = new_values.get(fact["field"], fact["value"])
            fact["quote"] = next(c["text"] for c in new_chunks if c["id"] == fact["chunk_id"])
        service = self.reopen(changed_facts)
        child = service.replace_document(self.rid, changed_source, "revised.txt", "Test", "Revenue revised", old.checkpoint_id)
        new = service.view(child)
        self.assertFalse(new.state["reuse_facts"])
        self.assertEqual(new.state["facts"][4]["value"], "45000000")
        self.assertNotEqual(new.state["document_hash"], old.state["document_hash"])
        self.assertEqual(new.status, "pending")
        self.assertEqual(service.repository.revision(child)["source"], changed_source)
        confirmed = self.confirm(child, service=service)
        self.assertEqual(confirmed.state["calculations"]["enterprise_value"], 70_000_000)
        self.assertEqual(confirmed.state["calculations"]["equity_value"], 55_000_000)
        impact = DocumentComparator.calculation_changes(old.state, confirmed.state)
        self.assertEqual(impact["deltas"][1]["change"], -13_000_000)

    def test_identical_document_is_noop_even_with_different_filename(self):
        old = self.approve()
        with self.assertRaisesRegex(ValueError, "identical bytes"):
            self.service.replace_document(self.rid, self.source, "renamed.txt", "Test", "Rename", old.checkpoint_id)
        self.assertEqual(self.service.view(self.rid).status, "approved")
        self.assertEqual(len(self.service.repository.revisions(old.revision["case_id"])), 1)

    def test_unreadable_upload_does_not_invalidate_approval(self):
        old = self.approve()
        with self.assertRaises(ValueError):
            self.service.replace_document(self.rid, b" ", "empty.txt", "Test", "Bad upload", old.checkpoint_id)
        self.assertEqual(self.service.view(self.rid).status, "approved")

    def test_stale_checkpoint_cannot_approve_or_branch(self):
        first = self.service.view(self.rid)
        self.confirm()
        with self.assertRaises(StaleRevisionError):
            self.service.resume(self.rid, {"decision": "approved", "reviewer": "Test", "note": "Old page"}, first.checkpoint_id)
        with self.assertRaises(StaleRevisionError):
            self.service.revise(self.rid, "Test", "Old page", first.checkpoint_id)

    def test_superseded_revision_is_read_only(self):
        old = self.approve()
        self.service.revise(self.rid, "Test", "Fresh review", old.checkpoint_id)
        with self.assertRaises(StaleRevisionError):
            self.service.resume(self.rid, {"decision": "approved", "reviewer": "Test", "note": "Old"}, old.checkpoint_id)
        with self.assertRaises(StaleRevisionError):
            self.service.revise(self.rid, "Test", "Old", old.checkpoint_id)

    def test_concurrent_case_operation_is_rejected(self):
        view = self.service.view(self.rid)
        with self.service.repository.lock(view.revision["case_id"]):
            with self.assertRaises(CaseBusyError):
                self.reopen().revise(self.rid, "Test", "Competing tab", view.checkpoint_id)

    def test_failed_processing_after_revision_creation_stays_unapproved_and_recovers(self):
        old = self.approve()
        with patch.object(self.service, "_run_initial", side_effect=RuntimeError("Simulated crash")):
            with self.assertRaises(RuntimeError):
                self.service.revise(self.rid, "Test", "New assumptions", old.checkpoint_id)
        case = self.service.repository.case(old.revision["case_id"])
        child = case["active_revision_id"]
        self.assertNotEqual(child, self.rid)
        new_service = self.reopen()
        view = new_service.view(child)
        self.assertEqual(view.status, "pending")
        self.assertFalse(view.state)
        restored = new_service.retry_processing(child, view.checkpoint_id)
        self.assertEqual(restored.next, ("input_review",))
        self.assertEqual(new_service.view(self.rid).status, "superseded")

    def test_case_titles_are_parameterized_and_source_history_is_immutable(self):
        original = self.service.repository.revision(self.rid)
        self.service.create("Name'); DROP TABLE cases; --", self.source, "source.txt", "offline", "Test", "SQL text")
        self.assertEqual(len(self.service.repository.list_cases()), 2)
        self.assertEqual(self.service.repository.revision(self.rid), original)

    def test_unknown_database_version_is_rejected_without_overwriting(self):
        path = self.service.repository.path
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            CaseRepository(self.directory.name)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)

    def test_content_binding_detects_post_approval_changes(self):
        state = self.approve().state
        self.assertEqual(ApprovalPolicy.status(state), "approved")
        for key, value in [("document_hash", "changed"), ("revision_id", "other"),
                           ("assumptions", {**ASSUMPTIONS, "multiple": 12}),
                           ("memo", {"observations": []}), ("facts", [])]:
            altered = deepcopy(state); altered[key] = value
            self.assertEqual(ApprovalPolicy.status(altered), "stale_approval", key)
        changed_audit = deepcopy(state); changed_audit["audit"].append({"action": "viewed"})
        self.assertEqual(ApprovalPolicy.status(changed_audit), "approved")


class ComparisonTests(unittest.TestCase):
    def test_text_changes_have_original_page_references(self):
        before = [{"page": 1, "text": "Revenue: 50\nEBITDA: 8"}, {"page": 2, "text": "Debt: 12"}]
        after = [{"page": 1, "text": "Revenue: 45\nEBITDA: 8"}, {"page": 2, "text": "Debt: 15\nNew contract"}]
        result = DocumentComparator.text_changes(before, after)
        self.assertIn("-Revenue: 50", result["diff"])
        self.assertIn("+Revenue: 45", result["diff"])
        self.assertTrue(any(line["page"] == 2 for c in result["changes"] for line in c["after"]))

    def test_added_removed_and_html_text_remain_plain_data(self):
        before = [{"page": 1, "text": "Remove me\nKeep me"}]
        after = [{"page": 1, "text": "Keep me\n<script>alert('x')</script>"}]
        result = DocumentComparator.text_changes(before, after)
        self.assertEqual({c["operation"] for c in result["changes"]}, {"delete", "insert"})
        self.assertIn("<script>", result["diff"])

    def test_fact_changes_distinguish_value_changes_from_evidence_changes(self):
        facts = fixture()[2]
        changed = deepcopy(facts)
        changed[0]["quote"] = "Different source wording"
        changed[4]["value"] = "45000000"
        changes = DocumentComparator.fact_changes({"facts": facts}, {"facts": changed})
        self.assertEqual(changes[0]["category"], "evidence_or_note")
        self.assertEqual(changes[1]["category"], "value_or_status")

    def test_calculation_deltas_require_comparable_confirmed_results(self):
        company = {"company_name": "Example", "currency": "USD", "period": "FY2025"}
        old = {"company": company, "calculations": {"enterprise_value": 80., "equity_value": 68.}, "assumptions": {"multiple": 10}}
        new = {"company": company, "calculations": {"enterprise_value": 70., "equity_value": 55.}, "assumptions": {"multiple": 10}}
        result = DocumentComparator.calculation_changes(old, new)
        self.assertTrue(result["available"])
        self.assertEqual(result["deltas"][1]["change"], -13.)
        for key, value in [("company_name", "Other"), ("currency", "EUR"), ("period", "FY2026")]:
            altered = deepcopy(new); altered["company"][key] = value
            self.assertFalse(DocumentComparator.calculation_changes(old, altered)["available"])
        self.assertFalse(DocumentComparator.calculation_changes(old, {})["available"])
