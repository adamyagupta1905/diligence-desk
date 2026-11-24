"""Offline regression tests: real components/graph, with model calls replaced."""
from copy import deepcopy
import io
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
from langgraph.types import Command
from google.genai import types
from pypdf import PdfWriter
from deal_screening.ai import FactExtractor, MemoWriter
from deal_screening.audit import AuditLog
from deal_screening.documents import DocumentReader, PassageRetriever
from deal_screening.finance import ValuationEngine
from deal_screening.llm import GeminiClient, safe_api_error
from deal_screening.models import Extraction, Memo, MemoPoint
from deal_screening.reporting import report_text
from deal_screening.validation import InputValidator
from deal_screening.workflow import ScreeningWorkflow
from helpers import CASES, ASSUMPTIONS, fixture, FakeExtractor, FakeMemoWriter
from evaluate_extraction import compare


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.facts = fixture()[2]

    def test_empty_notes_are_not_corrections(self):
        edited = deepcopy(self.facts)
        for fact in edited:
            fact["note"] = None
        original = deepcopy(self.facts)
        self.assertEqual(AuditLog.changes(self.facts, edited), [])
        self.assertEqual(self.facts, original)

    def test_display_changes_are_not_corrections(self):
        edited = deepcopy(self.facts)
        next(f for f in edited if f["field"] == "revenue")["value"] = "50000000.00"
        edited.reverse()
        self.assertEqual(AuditLog.changes(self.facts, edited), [])

    def test_real_value_change_is_recorded(self):
        edited = deepcopy(self.facts)
        next(f for f in edited if f["field"] == "revenue")["value"] = "55000000"
        changes = AuditLog.changes(self.facts, edited)
        self.assertEqual([c["field"] for c in changes], ["revenue"])
        self.assertEqual(changes[0]["changed_properties"], ["value"])
        self.assertEqual(changes[0]["before"]["value"], "50000000")
        self.assertEqual(changes[0]["after"]["value"], "55000000")

    def test_real_note_and_citation_change_is_recorded(self):
        edited = deepcopy(self.facts)
        edited[0]["note"] = "Checked legal entity name."
        edited[1]["chunk_id"] = "S999"
        self.assertEqual(len(AuditLog.changes(self.facts, edited)), 2)


class DocumentAndValidationTests(unittest.TestCase):
    def test_evaluator_accepts_known_answers_and_detects_wrong_units_or_silent_conflict(self):
        for case in CASES:
            _, chunks, facts = fixture(case)
            self.assertEqual(compare(case, facts, chunks), [])
        _, chunks, facts = fixture(CASES[3])
        next(f for f in facts if f["field"] == "revenue")["value"] = "50000"
        self.assertTrue(compare(CASES[3], facts, chunks))
        _, chunks, facts = fixture(CASES[2])
        revenue = next(f for f in facts if f["field"] == "revenue")
        revenue.update(status="found", value="50000000", chunk_id=chunks[0]["id"],
                       quote=chunks[0]["text"], currency="USD", period="FY2025")
        self.assertTrue(compare(CASES[2], facts, chunks))

    def test_all_case_fixtures_have_expected_blocking_behavior(self):
        for case in CASES:
            with self.subTest(document=case["file"]):
                _, chunks, facts = fixture(case)
                issues, _ = InputValidator.facts(facts, chunks)
                self.assertEqual(bool(issues), case["blocked"], issues)
                for field in case["blocked_fields"]:
                    self.assertTrue(any(field in issue for issue in issues))

    def test_keyword_retrieval_preserves_page_and_source_ids(self):
        _, chunks, _ = fixture()
        passages = PassageRetriever().retrieve(chunks)
        self.assertEqual({c["id"] for c in passages}, {"S001", "S002"})
        self.assertEqual({c["page"] for c in passages}, {1, 2})

    def test_unknown_citation_or_nonexistent_quote_blocks(self):
        _, chunks, facts = fixture()
        for key, bad in [("chunk_id", "S999"), ("quote", "A fabricated sentence")]:
            edited = deepcopy(facts)
            edited[0][key] = bad
            self.assertTrue(InputValidator.facts(edited, chunks)[0])

    def test_period_currency_and_invalid_numbers_block(self):
        _, chunks, facts = fixture()
        for key, bad in [("period", "FY2024"), ("currency", "EUR"),
                         ("value", "NaN"), ("value", "0"), ("value", "1e309")]:
            edited = deepcopy(facts)
            next(f for f in edited if f["field"] == "ebitda")[key] = bad
            self.assertTrue(InputValidator.facts(edited, chunks)[0])

    def test_net_debt_may_state_the_period_end_date(self):
        from deal_screening.validation import period_matches
        long = "year ended 31 December 2024"
        self.assertTrue(period_matches("net_debt", "31 December 2024", long))
        self.assertTrue(period_matches("net_debt", long, long))
        self.assertTrue(period_matches("net_debt", "at 31 December 2024", long))
        self.assertTrue(period_matches("net_debt", "As at 31 December 2024", long))
        # A bare year does not say when the year ends, so the analyst must confirm.
        self.assertFalse(period_matches("net_debt", "31 December 2025", "2025"))
        # Wrong date, a non-end date, flows and bare labels still need an exact match.
        self.assertFalse(period_matches("net_debt", "31 December 2023", long))
        self.assertFalse(period_matches("net_debt", "2024", long))
        self.assertFalse(period_matches("net_debt", "1 January 2024", "year ended 31 December 2024"))
        self.assertFalse(period_matches("ebitda", "31 December 2024", long))
        self.assertFalse(period_matches("net_debt", "31 December 2025", "FY2025"))
        self.assertFalse(period_matches("net_debt", None, long))

    def test_duplicate_rows_block(self):
        _, chunks, facts = fixture()
        facts[-1] = deepcopy(facts[0])
        self.assertTrue(InputValidator.facts(facts, chunks)[0])

    def test_optional_missing_stays_unknown(self):
        _, chunks, facts = fixture(CASES[-1])
        issues, company = InputValidator.facts(facts, chunks)
        self.assertFalse(issues)
        self.assertIsNone(company["customer_concentration"])
        flags = ValuationEngine.flags(company, ValuationEngine.calculate(company, ASSUMPTIONS))
        self.assertTrue(any("concentration is unknown" in f for f in flags))
        self.assertTrue(any("growth is unknown" in f for f in flags))

    def test_blank_or_scanned_pdf_and_empty_text_are_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        stream = io.BytesIO(); writer.write(stream)
        for data, name in [(stream.getvalue(), "blank.pdf"), (b"  ", "empty.txt")]:
            with self.assertRaises(ValueError):
                DocumentReader.read(data, name)


class FinancialTests(unittest.TestCase):
    def test_normalized_units_all_produce_same_valuation(self):
        for case in (CASES[0], CASES[3], CASES[4]):
            _, chunks, facts = fixture(case)
            issues, company = InputValidator.facts(facts, chunks)
            self.assertFalse(issues)
            result = ValuationEngine.calculate(company, ASSUMPTIONS)
            self.assertEqual(result["enterprise_value"], 80_000_000)
            self.assertEqual(result["equity_value"], 68_000_000)
            self.assertAlmostEqual(result["stress_ebitda"], 5_850_000)
            self.assertAlmostEqual(result["stress_equity_value"], 34_800_000)

    def test_nonpositive_stress_ebitda_withholds_valuation(self):
        _, chunks, facts = fixture()
        company = InputValidator.facts(facts, chunks)[1]
        result = ValuationEngine.calculate(company, {**ASSUMPTIONS, "margin_shock_pp": -30.})
        self.assertIsNone(result["stress_enterprise_value"])
        self.assertIsNone(result["stress_equity_value"])

    def test_net_cash_and_invalid_assumptions(self):
        _, chunks, facts = fixture()
        company = InputValidator.facts(facts, chunks)[1]
        result = ValuationEngine.calculate({**company, "net_debt": -2_000_000}, ASSUMPTIONS)
        self.assertEqual(result["equity_value"], 82_000_000)
        for bad in [{"multiple": 0}, {"multiple": math.nan}, {"revenue_shock": -1},
                    {"multiple_shock": -10}]:
            with self.assertRaises(ValueError):
                ValuationEngine.calculate(company, {**ASSUMPTIONS, **bad})


class ModelBoundaryTests(unittest.TestCase):
    def test_gemini_sends_json_schema_not_legacy_response_schema(self):
        memo = Memo(observations=[], diligence_questions=[])
        response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)],
                                   text=memo.model_dump_json())
        with patch("deal_screening.llm.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = response
            GeminiClient("offline-placeholder").generate("Test", Memo, "offline-model")
            config = generate.call_args.kwargs["config"]
            self.assertIsNone(config.response_schema)
            self.assertEqual(config.response_json_schema, Memo.model_json_schema())

    def test_api_error_redacts_key(self):
        error = SimpleNamespace(code=400, message="Key AIzaSECRET_123 was rejected")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSECRET_123"}):
            message = safe_api_error(error)
        self.assertIn("HTTP 400", message)
        self.assertNotIn("AIzaSECRET_123", message)

    def test_extractor_rejects_duplicate_or_missing_fields(self):
        facts = fixture()[2]
        class Client:
            def generate(self, *args):
                return Extraction(facts=facts[:-1])
        with self.assertRaises(ValueError):
            FactExtractor(Client()).extract([], "offline")

    def test_memo_rejects_invented_references(self):
        class Client:
            def generate(self, *args):
                return Memo(observations=[MemoPoint(text="Claim", references=["S999"])]*3,
                            diligence_questions=["A?", "B?", "C?"])
        state = {"company": {}, "facts": [], "assumptions": {}, "calculations": {},
                 "flags": [], "feedback": "", "chunks": [], "model_name": "offline"}
        with self.assertRaises(ValueError):
            MemoWriter(Client()).draft(state)


class WorkflowTests(unittest.TestCase):
    def start(self, case=CASES[0], writer=None):
        pages, _, facts = fixture(case)
        writer = writer or FakeMemoWriter()
        graph = ScreeningWorkflow(FakeExtractor(facts), writer).build()
        config = {"configurable": {"thread_id": "test"}}
        graph.invoke({"pages": pages, "audit": [], "feedback": "", "model_name": "offline",
                      "document_name": case["file"], "document_hash": "offline-fixture"}, config)
        return graph, config, facts, writer

    def confirm(self, graph, config, facts):
        return graph.invoke(Command(resume={"facts": facts, "assumptions": ASSUMPTIONS,
                            "reviewer": "Test analyst", "note": "Evidence checked"}), config)

    def test_real_pauses_audit_fix_revision_approval_and_report(self):
        graph, config, facts, writer = self.start()
        self.assertEqual(graph.get_state(config).next, ("input_review",))
        self.assertNotIn("calculations", graph.get_state(config).values)
        edited = deepcopy(facts)
        for f in edited:
            f["note"] = None  # Reproduce the original UI serialization bug.
        self.confirm(graph, config, edited)
        state = graph.get_state(config).values
        confirmation = next(e for e in state["audit"] if e["action"] == "inputs_confirmed")
        self.assertEqual(confirmation["corrected_fields"], [])
        self.assertEqual(confirmation["fact_changes"], [])
        self.assertEqual(graph.get_state(config).next, ("memo_review",))
        graph.invoke(Command(resume={"decision": "revise", "reviewer": "Test", "note": "Revise"}), config)
        self.assertEqual(writer.calls, 2)
        self.assertEqual(graph.get_state(config).next, ("memo_review",))
        graph.invoke(Command(resume={"decision": "approved", "reviewer": "Test", "note": "Checked"}), config)
        self.assertFalse(graph.get_state(config).next)
        report = report_text(graph.get_state(config).values)
        self.assertIn("Status: approved", report)
        self.assertIn('"corrected_fields": []', report)

    def test_missing_or_conflicting_required_inputs_cannot_bypass_review(self):
        for case in (CASES[1], CASES[2], CASES[5], CASES[6]):
            graph, config, facts, writer = self.start(case)
            with self.assertRaises(ValueError):
                self.confirm(graph, config, facts)
            self.assertNotIn("calculations", graph.get_state(config).values)
            self.assertEqual(writer.calls, 0)

    def test_provider_failure_preserves_math_and_blocks_memo_approval(self):
        graph, config, facts, _ = self.start(writer=FakeMemoWriter(fail=True))
        self.confirm(graph, config, facts)
        state = graph.get_state(config).values
        self.assertEqual(state["calculations"]["equity_value"], 68_000_000)
        self.assertTrue(state["memo_error"])
        with self.assertRaises(ValueError):
            graph.invoke(Command(resume={"decision": "approved", "reviewer": "Test", "note": "Checked"}), config)

    def test_rejection_ends_workflow(self):
        graph, config, facts, _ = self.start()
        self.confirm(graph, config, facts)
        graph.invoke(Command(resume={"decision": "rejected", "reviewer": "Test", "note": "Reject"}), config)
        self.assertFalse(graph.get_state(config).next)
        self.assertEqual(graph.get_state(config).values["decision"], "rejected")


if __name__ == "__main__":
    unittest.main()
