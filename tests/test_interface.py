"""Actual Streamlit controls, temporary SQLite storage, and a simulated model."""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from google.genai import types
from deal_screening.ai import FactExtractor, MemoWriter
from deal_screening.cases import CaseService
from deal_screening.documents import DocumentReader
from deal_screening.examples import SAMPLE_TEXT
from deal_screening.models import Extraction, Memo, MemoPoint
from deal_screening.storage import CaseRepository
from deal_screening.workflow import ScreeningWorkflow
from helpers import fixture, FakeExtractor, FakeMemoWriter


class InterfaceTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        facts = fixture()[2]
        chunks = DocumentReader.chunks(DocumentReader.read(SAMPLE_TEXT.encode(), "builtin.txt"))
        for fact in facts:
            fact["quote"] = next(c["text"] for c in chunks if c["id"] == fact["chunk_id"])
        def generate(**kwargs):
            schema = kwargs["config"].response_json_schema
            if "facts" in schema["properties"]:
                text = Extraction(facts=facts).model_dump_json()
            else:
                text = Memo(observations=[MemoPoint(text="Offline observation", references=["S001"])]*3,
                            diligence_questions=["A?", "B?", "C?"]).model_dump_json()
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)], text=text)
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", ["app.py"]), patch.dict(
            "os.environ", {"GEMINI_API_KEY": "offline-placeholder", "DEAL_SCREENING_DATA_DIR": directory}
        ), patch("deal_screening.llm.genai.Client") as client:
            client.return_value.__enter__.return_value.models.generate_content.side_effect = generate
            service = CaseService(CaseRepository(directory),
                                   lambda: ScreeningWorkflow(FakeExtractor(facts), FakeMemoWriter()))
            yield service

    def new_app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        self.assertFalse(app.exception)
        return app

    def start(self, app, service):
        app.text_input(key="new_author").input("Test analyst")
        next(b for b in app.button if b.label == "Start new screening").click().run()
        self.assertFalse(app.exception)
        case = service.repository.list_cases()[0]
        return case["id"], case["active_revision_id"]

    def confirm(self, app, rid):
        app.text_input(key="input_reviewer_" + rid).input("Test analyst")
        app.text_area(key="input_note_" + rid).input("Checked source, units and periods")
        app.checkbox(key="input_check_" + rid).check()
        next(b for b in app.button if b.label == "Confirm inputs and calculate").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 3)

    def approve(self, app, rid):
        app.text_input(key="memo_reviewer_" + rid).input("Test analyst")
        app.text_area(key="memo_note_" + rid).input("Memo checked")
        next(c for c in app.checkbox if c.key.startswith("memo_check_" + rid)).check()
        next(b for b in app.button if b.label == "Submit review").click().run()
        self.assertFalse(app.exception)

    def test_start_confirm_approve_and_reopen_saved_case(self):
        with self.environment() as service:
            app = self.new_app()
            case_id, rid = self.start(app, service)
            self.confirm(app, rid)
            audit = next(e for e in service.view(rid).state["audit"] if e["action"] == "inputs_confirmed")
            self.assertEqual(audit["corrected_fields"], [])
            self.approve(app, rid)
            self.assertEqual(service.view(rid).status, "approved")
            # A fresh browser session has no graph/session objects to reuse.
            reopened = self.new_app()
            reopened.selectbox(key="case_selector").select(case_id).run()
            self.assertFalse(reopened.exception)
            self.assertTrue(any("Current revision approved" in e.value for e in reopened.success))
            self.assertFalse(any(b.label == "Submit review" for b in reopened.button))

    def test_dcf_forecast_form_calculates_and_preserves_review_flow(self):
        with self.environment() as service:
            app = self.new_app()
            _, rid = self.start(app, service)
            app.selectbox(key="method_" + rid).select("dcf").run()
            self.assertFalse(app.exception)
            self.assertTrue(any(n.label == "WACC (%)" for n in app.number_input))
            self.confirm(app, rid)
            state = service.view(rid).state
            self.assertEqual(state["assumptions"]["valuation_method"], "dcf")
            self.assertEqual(len(state["calculations"]["projections"]), 5)
            self.approve(app, rid)
            self.assertEqual(service.view(rid).status, "approved")

    def test_detailed_dcf_controls_persist_schedules_and_approval(self):
        with self.environment() as service:
            app = self.new_app()
            case_id, rid = self.start(app, service)
            app.selectbox(key="method_" + rid).select("dcf").run()
            app.selectbox(key="dcf_mode_" + rid).select("detailed").run()
            self.assertFalse(app.exception)
            self.confirm(app, rid)
            state = service.view(rid).state
            self.assertEqual(state["assumptions"]["operating_model"], "detailed")
            row = state["calculations"]["projections"][0]
            self.assertAlmostEqual(row["ebit"], row["revenue"] * .20)
            self.assertIn("receivables", row)
            self.approve(app, rid)
            reopened = self.new_app()
            reopened.selectbox(key="case_selector").select(case_id).run()
            self.assertFalse(reopened.exception)
            self.assertEqual(service.view(rid).status, "approved")

    def test_fixed_asset_controls_schedules_restart_and_revision(self):
        from copy import deepcopy
        from deal_screening.approvals import ApprovalPolicy
        with self.environment() as service:
            app = self.new_app()
            case_id, rid = self.start(app, service)
            app.selectbox(key="method_" + rid).select("dcf").run()
            app.selectbox(key="dcf_mode_" + rid).select("detailed").run()
            app.selectbox(key="asset_mode_" + rid).select("fixed_assets").run()
            self.assertFalse(app.exception)
            next(n for n in app.number_input if n.label.startswith("Opening depreciable")).set_value(10_000_000.)
            self.confirm(app, rid)
            state = service.view(rid).state
            self.assertEqual(state["assumptions"]["depreciation_model"], "fixed_assets")
            r = state["calculations"]["projections"][0]
            self.assertAlmostEqual(r["depreciation"], 2_224_000.)
            self.assertAlmostEqual(r["closing_ppe"], 10_016_000.)
            self.assertAlmostEqual(r["ebitda"], 11_200_000.)
            self.assertAlmostEqual(r["ebit"], 8_976_000.)
            self.assertAlmostEqual(r["ppe_rollforward_difference"], 0)
            self.approve(app, rid)
            state = service.view(rid).state
            changed = deepcopy(state)
            changed["assumptions"]["fixed_assets"]["new_asset_life"] = 10.
            self.assertEqual(ApprovalPolicy.status(changed), "stale_approval")
            reopened = self.new_app()
            reopened.selectbox(key="case_selector").select(case_id).run()
            self.assertFalse(reopened.exception)
            self.assertEqual(service.view(rid).status, "approved")
            reopened.text_input(key="revision_author").input("Test analyst")
            reopened.text_area(key="revision_reason").input("Review useful lives")
            reopened.checkbox(key="revision_check").check()
            next(b for b in reopened.button if b.label == "Create revision").click().run()
            self.assertFalse(reopened.exception)
            child = service.repository.case(case_id)["active_revision_id"]
            self.assertEqual(reopened.selectbox(key="asset_mode_" + child).value, "fixed_assets")
            self.assertEqual(service.view(child).status, "pending")
            self.confirm(reopened, child)
            self.assertAlmostEqual(service.view(child).state["calculations"]["projections"][0]["closing_ppe"], 10_016_000.)

    def test_linked_statement_form_and_saved_outputs(self):
        from deal_screening.three_statements import OPENING
        with self.environment() as service:
            app = self.new_app()
            case_id, rid = self.start(app, service)
            app.selectbox(key="method_" + rid).select("dcf").run()
            app.selectbox(key="dcf_mode_" + rid).select("detailed").run()
            app.selectbox(key="asset_mode_" + rid).select("fixed_assets").run()
            app.checkbox(key="linked_" + rid).check().run()
            self.assertFalse(app.exception)
            next(n for n in app.number_input if n.label.startswith("Opening depreciable")).set_value(10_000_000.)
            opening=dict(cash=5_000_000.,receivables=5_000_000.,inventory=2_000_000.,other_operating_assets=1_000_000.,
                         ppe=10_000_000.,other_assets=10_000_000.,payables=2_000_000.,other_operating_liabilities=1_000_000.,
                         debt=17_000_000.,other_liabilities=3_000_000.,equity=10_000_000.)
            app.session_state["opening_statements_"+rid] = {"edited_rows": {i:{"value":opening[k]} for i,k in enumerate(OPENING)},"added_rows":[],"deleted_rows":[]}
            next(b for b in app.button if b.label == "Preview opening statement balances").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(service.view(rid).next,("input_review",))
            self.confirm(app,rid)
            state=service.view(rid).state
            self.assertEqual(state["calculations"]["three_statements"]["status"],"balanced")
            self.assertEqual(state["assumptions"]["three_statements"]["opening"],opening)
            self.approve(app,rid)
            reopened=self.new_app()
            reopened.selectbox(key="case_selector").select(case_id).run()
            self.assertFalse(reopened.exception)
            self.assertEqual(service.view(rid).status,"approved")

    def test_revision_returns_to_review_and_history_cannot_approve(self):
        with self.environment() as service:
            app = self.new_app()
            case_id, rid = self.start(app, service)
            self.confirm(app, rid)
            self.approve(app, rid)
            app.text_input(key="revision_author").input("Test analyst")
            app.text_area(key="revision_reason").input("Reduce selected multiple")
            app.checkbox(key="revision_check").check()
            next(b for b in app.button if b.label == "Create revision").click().run()
            self.assertFalse(app.exception)
            child = service.repository.case(case_id)["active_revision_id"]
            self.assertNotEqual(child, rid)
            self.assertEqual(len(app.metric), 0)
            self.assertEqual(service.view(child).status, "pending")
            app.number_input[0].set_value(9.)
            self.confirm(app, child)
            self.assertEqual(service.view(child).state["calculations"]["equity_value"], 60_000_000)
            app.selectbox(key="revision_selector_" + case_id).select(rid).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Historical revision" in e.value for e in app.warning))
            self.assertFalse(any(b.label in {"Submit review", "Confirm inputs and calculate", "Create revision"} for b in app.button))

    def test_new_memo_draft_requires_fresh_confirmation_checkbox(self):
        with self.environment() as service:
            app = self.new_app()
            _, rid = self.start(app, service)
            self.confirm(app, rid)
            app.text_input(key="memo_reviewer_" + rid).input("Test")
            app.text_area(key="memo_note_" + rid).input("Revise wording")
            next(c for c in app.checkbox if c.key.startswith("memo_check_" + rid)).check()
            next(s for s in app.selectbox if s.label == "Decision").select("revise")
            next(b for b in app.button if b.label == "Submit review").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(next(c for c in app.checkbox if c.key.startswith("memo_check_" + rid)).value)
            self.assertNotEqual(service.view(rid).status, "approved")

    def test_outdated_ui_form_cannot_approve_another_tabs_new_memo(self):
        with self.environment() as service:
            app = self.new_app()
            _, rid = self.start(app, service)
            self.confirm(app, rid)
            app.text_input(key="memo_reviewer_" + rid).input("Old tab")
            app.text_area(key="memo_note_" + rid).input("Reviewed old draft")
            next(c for c in app.checkbox if c.key.startswith("memo_check_" + rid)).check()
            old_view = service.view(rid)
            service.resume(rid, {"decision": "revise", "reviewer": "Other tab", "note": "New draft"}, old_view.checkpoint_id)
            next(b for b in app.button if b.label == "Submit review").click().run()
            self.assertFalse(app.exception)
            self.assertNotEqual(service.view(rid).status, "approved")

    def test_historical_ledger_review_precedes_valuation_and_uses_selected_year(self):
        from test_ledger import FakeHistoricalExtractor, SOURCE
        with self.environment() as service:
            history=FakeHistoricalExtractor()
            factory=lambda:ScreeningWorkflow(FakeExtractor(fixture()[2]),FakeMemoWriter(),ledger_extractor=history)
            historical_service=CaseService(service.repository,factory)
            rid=historical_service.create('Three-year history',SOURCE.read_bytes(),SOURCE.name,'fake','Test','History test',ledger_enabled=True)
            view=historical_service.view(rid);case_id=view.revision['case_id']
            app=self.new_app()
            app.selectbox(key='case_selector').select(case_id).run()
            self.assertFalse(app.exception)
            self.assertFalse(any(b.label=='Confirm inputs and calculate' for b in app.button))
            prefix=rid+'_'+view.checkpoint_id
            app.text_input(key='ledger_reviewer_'+prefix).input('Test analyst')
            app.text_area(key='ledger_note_'+prefix).input('Checked annual headings, thousands, signs and statement reconciliations.')
            app.checkbox(key='ledger_check_'+prefix).check()
            next(b for b in app.button if b.label=='Preview historical normalization and checks').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(historical_service.view(rid).next,('ledger_review',))
            next(b for b in app.button if b.label=='Confirm historical ledger').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(historical_service.view(rid).next,('input_review',))
            self.confirm(app,rid)
            self.assertEqual(historical_service.view(rid).state['calculations']['equity_value'],180_000_000)
            self.approve(app,rid)
            self.assertEqual(historical_service.view(rid).status,'approved')
