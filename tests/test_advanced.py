"""Independent DCF arithmetic, loss routing, OCR provenance and review controls."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.types import Command
from deal_screening.dcf import DCFEngine, default_dcf
from deal_screening.documents import DocumentReader
from deal_screening.finance import ValuationEngine
from deal_screening.validation import InputValidator
from deal_screening.comparison import DocumentComparator
from deal_screening.workflow import ScreeningWorkflow
from deal_screening.cases import CaseService
from deal_screening.storage import CaseRepository
from deal_screening.ocr import LocalOCREngine
from helpers import fixture, FakeExtractor, FakeMemoWriter, ASSUMPTIONS, ROOT


def flat_assumptions():
    return {"valuation_method": "dcf", "wacc": .10, "terminal_growth": 0.,
            "terminal_roic": .15, "tax_rate": .25, "opening_nwc": 0., "opening_nol": 0.,
            "nol_utilization_limit": 1., "dcf_growth_shock": 0., "dcf_margin_shock": 0., "dcf_wacc_shock": 0.,
            "forecast": [{"year": i, "revenue_growth": 0., "ebitda_margin": .20,
                          "da_ratio": 0., "capex_ratio": 0., "nwc_ratio": 0.} for i in range(1,4)]}


class DCFTests(unittest.TestCase):
    def setUp(self):
        self.company = {"revenue": 100., "ebitda": 20., "net_debt": 10.}
        self.a = flat_assumptions()

    def test_flat_perpetuity_matches_independent_closed_form(self):
        result = DCFEngine.calculate(self.company, self.a)
        # 20 EBIT - 5 cash taxes = 15 FCFF forever; PV=15/10%=150.
        self.assertAlmostEqual(result["enterprise_value"], 150.)
        self.assertAlmostEqual(result["equity_value"], 140.)
        self.assertTrue(all(row["fcff"] == 15 for row in result["projections"]))
        self.assertAlmostEqual(result["terminal_fcff"], 15.)

    def test_terminal_reinvestment_uses_growth_over_roic(self):
        self.a.update(terminal_growth=.02, terminal_roic=.10)
        result = DCFEngine.calculate(self.company, self.a)
        # Next-year NOPAT=20*1.02*.75=15.3; reinvestment=.02/.10=.2.
        self.assertAlmostEqual(result["terminal_fcff"], 12.24)
        self.assertAlmostEqual(result["terminal_value"], 153.)
        expected = sum(15 / 1.1 ** y for y in (1,2,3)) + 153 / 1.1 ** 3
        self.assertAlmostEqual(result["enterprise_value"], expected)

    def test_negative_cash_flow_and_tax_loss_pool_are_not_removed(self):
        self.a.update(opening_nol=5., nol_utilization_limit=.5)
        self.a["forecast"][0]["ebitda_margin"] = -.10
        result = DCFEngine.calculate({**self.company, "ebitda": -10}, self.a)
        rows = result["projections"]
        self.assertEqual(rows[0]["fcff"], -10.)
        self.assertEqual(rows[0]["cash_taxes"], 0.)
        self.assertEqual(rows[0]["closing_loss_pool"], 15.)
        self.assertEqual(rows[1]["loss_pool_used"], 10.)
        self.assertEqual(rows[1]["cash_taxes"], 2.5)
        self.assertEqual(rows[2]["cash_taxes"], 3.75)
        self.assertEqual(result["peak_undiscounted_fcff_deficit"], 10.)
        expected = -10/1.1 + 17.5/1.1**2 + 16.25/1.1**3 + 150/1.1**3
        self.assertAlmostEqual(result["enterprise_value"], expected)

    def test_working_capital_uses_changes_and_capex_da_bridge(self):
        for row in self.a["forecast"]:
            row.update(da_ratio=.02, capex_ratio=.03, nwc_ratio=.10)
        result = DCFEngine.calculate(self.company, self.a)
        first = result["projections"][0]
        self.assertEqual(first["ebit"], 18.)
        self.assertEqual(first["change_nwc"], 10.)
        self.assertAlmostEqual(first["fcff"], 18-4.5+2-3-10)
        self.assertEqual(result["projections"][1]["change_nwc"], 0.)

    def test_dcf_rejects_invalid_rates_missing_drivers_and_nonconverged_business(self):
        for key, value in [("wacc", 0), ("terminal_growth", .10), ("terminal_roic", 0),
                           ("opening_nol", -1), ("tax_rate", math.nan), ("tax_rate", True),
                           ("forecast", []), ("nol_utilization_limit", 1.1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                DCFEngine.calculate(self.company, {**self.a, key: value})
        bad = deepcopy(self.a)
        bad["forecast"][-1]["ebitda_margin"] = -.1
        with self.assertRaisesRegex(ValueError, "Final forecast"):
            DCFEngine.calculate(self.company, bad)
        with self.assertRaises(ValueError):
            DCFEngine.calculate({**self.company, "revenue": 0}, self.a)

    def test_unviable_downside_is_withheld_without_losing_base_value(self):
        result = DCFEngine.calculate(self.company, {**self.a, "dcf_margin_shock": -.3})
        self.assertAlmostEqual(result["enterprise_value"], 150.)
        self.assertIsNone(result["stress_enterprise_value"])
        self.assertIn("Final forecast", result["stress_error"])

    def test_higher_wacc_reduces_value_and_sensitivity_invalid_cells_are_explicit(self):
        base = DCFEngine.calculate(self.company, self.a)
        higher = DCFEngine.calculate(self.company, {**self.a, "wacc": .15})
        self.assertLess(higher["enterprise_value"], base["enterprise_value"])
        result = DCFEngine.calculate(self.company, {**self.a, "terminal_growth": .095})
        self.assertTrue(any(row["equity_value"] is None and row["error"] for row in result["sensitivity"]))

    def test_method_dispatch_accepts_losses_only_with_dcf(self):
        loss = {**self.company, "ebitda": -20.}
        self.assertEqual(ValuationEngine.calculate(loss, self.a)["valuation_method"], "dcf")
        with self.assertRaises(ValueError):
            ValuationEngine.calculate(loss, ASSUMPTIONS)
        _, chunks, facts = fixture()
        next(f for f in facts if f["field"] == "ebitda")["value"] = "-2000000"
        self.assertTrue(InputValidator.facts(facts, chunks)[0])
        self.assertFalse(InputValidator.facts(facts, chunks, method="dcf")[0])

    def test_unknown_historical_ebitda_is_not_filled_by_a_forecast(self):
        _, chunks, facts = fixture()
        f = next(f for f in facts if f["field"] == "ebitda")
        f.update(status="missing", value=None, quote=None, chunk_id=None)
        issues, company = InputValidator.facts(facts, chunks, method="dcf")
        self.assertFalse(issues)
        result = DCFEngine.calculate(company, default_dcf(company))
        self.assertIsNone(result["ebitda"])
        self.assertIsNone(result["margin"])

    def test_changing_valuation_method_withholds_like_for_like_delta(self):
        old = {"calculations": {"enterprise_value": 1}, "assumptions": {}}
        new = {"calculations": {"enterprise_value": 2}, "assumptions": {"valuation_method": "dcf"}}
        self.assertFalse(DocumentComparator.calculation_changes(old,new)["available"])


class OCRTests(unittest.TestCase):
    scan = ROOT / "output/pdf/12_scanned_loss_making_company.pdf"

    def test_actual_image_only_pdf_runs_local_ocr_with_provenance(self):
        from pypdf import PdfReader
        self.assertFalse(PdfReader(self.scan).pages[0].extract_text().strip())
        pages = DocumentReader.read(self.scan.read_bytes(), self.scan.name)
        self.assertEqual(pages[0]["extraction_method"], "ocr")
        self.assertIn("10.0 million", pages[0]["text"])
        self.assertIn("-2.0 million", pages[0]["text"])
        self.assertTrue(pages[0]["ocr_lines"])
        self.assertGreater(pages[0]["ocr_confidence"], 0)
        self.assertEqual(DocumentReader.chunks(pages)[0]["extraction_method"], "ocr")

    def test_native_pdf_does_not_call_ocr_unless_forced(self):
        import io
        from reportlab.pdfgen import canvas
        stream = io.BytesIO(); c = canvas.Canvas(stream)
        c.drawString(50,700,"Native readable company document with enough financial context. " * 2)
        c.showPage(); c.save(); data=stream.getvalue()
        engine = unittest.mock.Mock()
        pages=DocumentReader.read(data,"native.pdf",ocr_engine=engine)
        self.assertEqual(pages[0]["extraction_method"],"native")
        engine.recognize.assert_not_called()
        engine.recognize.return_value={"text":"Forced recognized text", "extraction_method":"ocr"}
        self.assertEqual(DocumentReader.read(data,"native.pdf",force_ocr=True,ocr_engine=engine)[0]["extraction_method"],"ocr")
        engine.recognize.assert_called_once()

    def test_missing_binary_and_unsupported_force_mode_are_clear_errors(self):
        with patch("deal_screening.ocr.shutil.which",return_value=None), patch("deal_screening.ocr.Path.is_file",return_value=False):
            with self.assertRaisesRegex(ValueError,"Tesseract"):
                LocalOCREngine.executable()
        with self.assertRaises(ValueError):
            DocumentReader.read(b"plain text","sample.txt",force_ocr=True)

    def test_ocr_figures_need_explicit_review_before_calculation(self):
        pages,_,facts=fixture();pages[0]["extraction_method"]="ocr"
        graph=ScreeningWorkflow(FakeExtractor(facts),FakeMemoWriter()).build()
        config={"configurable":{"thread_id":"ocr-review"}}
        graph.invoke({"pages":pages,"audit":[],"model_name":"offline","feedback":""},config)
        with self.assertRaisesRegex(ValueError,"original scanned pages"):
            graph.invoke(Command(resume={"facts":facts,"assumptions":ASSUMPTIONS,
                "reviewer":"Test","note":"Checked"}),config)
        self.assertNotIn("calculations",graph.get_state(config).values)

    def test_dcf_loss_case_survives_restart_and_requires_new_approval(self):
        pages,_,facts=fixture()
        next(f for f in facts if f["field"]=="ebitda")["value"]="-2000000"
        with tempfile.TemporaryDirectory() as directory:
            factory=lambda:ScreeningWorkflow(FakeExtractor(facts),FakeMemoWriter())
            service=CaseService(CaseRepository(directory),factory)
            rid=service.create("Loss DCF",(ROOT/"sample_documents/01_complete_millions.txt").read_bytes(),"source.txt","offline","Test","Start")
            view=service.view(rid)
            assumptions=default_dcf({"revenue":50_000_000,"ebitda":-2_000_000})
            view=service.resume(rid,{"facts":facts,"assumptions":assumptions,"reviewer":"Test","note":"Reviewed recovery drivers"},view.checkpoint_id)
            self.assertEqual(view.state["calculations"]["valuation_method"],"dcf")
            view=service.resume(rid,{"decision":"approved","reviewer":"Test","note":"Reviewed DCF"},view.checkpoint_id)
            reopened=CaseService(CaseRepository(directory),factory)
            self.assertEqual(reopened.view(rid).status,"approved")
            child=reopened.revise(rid,"Test","Change WACC",view.checkpoint_id)
            self.assertEqual(reopened.view(child).status,"pending")

    def test_force_ocr_same_source_creates_fresh_unapproved_revision(self):
        pages=DocumentReader.read(self.scan.read_bytes(),self.scan.name)
        values={"company_name":"Orion Automation Software Ltd.",
                "business":"Industrial maintenance software sold as annual subscriptions.",
                "currency":"USD","period":"FY2025","revenue":"10000000",
                "ebitda":"-2000000","net_debt":"1000000","revenue_growth":"0.25",
                "customer_concentration":"0.35"}
        facts=[{"field":field,"value":value,"status":"found","chunk_id":"S001",
                "quote":pages[0]["text"],"currency":"USD" if field in {"revenue","ebitda","net_debt"} else None,
                "period":"FY2025" if field in {"revenue","ebitda","net_debt"} else None,"note":""}
               for field,value in values.items()]
        # Recognition itself is tested above; exercise service/revision controls
        # using that real recognition result, without running the model.
        with tempfile.TemporaryDirectory() as directory, patch.object(LocalOCREngine,"recognize",return_value=pages[0]):
            factory=lambda:ScreeningWorkflow(FakeExtractor(facts),FakeMemoWriter())
            service=CaseService(CaseRepository(directory),factory)
            rid=service.create("OCR loss case",self.scan.read_bytes(),self.scan.name,"offline","Test","Start")
            view=service.view(rid)
            a=default_dcf({"revenue":10_000_000.,"ebitda":-2_000_000.})
            view=service.resume(rid,{"facts":facts,"assumptions":a,"ocr_reviewed":True,
                                    "reviewer":"Test","note":"Checked original scan"},view.checkpoint_id)
            self.assertAlmostEqual(view.state["calculations"]["equity_value"],8_500_150.211855773)
            view=service.resume(rid,{"decision":"approved","reviewer":"Test","note":"Reviewed"},view.checkpoint_id)
            child=service.replace_document(rid,self.scan.read_bytes(),self.scan.name,"Test","Reprocess scan",view.checkpoint_id,force_ocr=True)
            self.assertEqual(service.view(child).revision["kind"],"ocr")
            self.assertEqual(service.view(child).status,"pending")
            self.assertEqual(service.view(rid).status,"superseded")
