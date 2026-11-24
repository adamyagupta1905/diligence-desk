"""Known-answer history tests. Model fixtures test software, not AI accuracy."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deal_screening.approvals import ApprovalPolicy
from deal_screening.cases import CaseService
from deal_screening.documents import DocumentReader
from deal_screening.ledger import LedgerValidator, LedgerDiagnostics, LedgerBridge, normalize, METRICS, LedgerContext, StatementExtraction
from deal_screening.ledger_interface import csv_text
from deal_screening.ledger_steps import ledger_changes
from deal_screening.ledger_extraction import LedgerExtractor
from deal_screening.reporting import report_text
from deal_screening.storage import CaseRepository
from deal_screening.workflow import ScreeningWorkflow
from helpers import ROOT, FakeExtractor, FakeMemoWriter, fixture, ASSUMPTIONS

SOURCE=ROOT/'sample_documents/13_three_year_statements.txt'
FIXTURE=json.loads((ROOT/'tests/ledger_fixture.json').read_text())


def history_fixture():
    pages=DocumentReader.read(SOURCE.read_bytes(),SOURCE.name)
    return deepcopy(FIXTURE['context']),deepcopy(FIXTURE['rows']),DocumentReader.chunks(pages)


class FakeHistoricalExtractor:
    def __init__(self,fail=None):
        self.calls=[];self.fail=fail
    def discover(self,chunks,model):
        self.calls.append('context')
        if self.fail=='context':raise RuntimeError('Simulated unavailable discovery')
        return deepcopy(FIXTURE['context'])
    def statement(self,chunks,context,statement,model):
        self.calls.append(statement)
        if self.fail==statement:raise RuntimeError('Simulated unavailable statement')
        return [deepcopy(r) for r in FIXTURE['rows'] if METRICS[r['metric']][0]==statement],[c['id'] for c in chunks]


class LedgerDomainTests(unittest.TestCase):
    def test_normalization_keeps_raw_and_converts_scale_sign_and_ratio(self):
        ctx,rows,chunks=history_fixture()
        old=deepcopy(rows)
        errors,normalized=LedgerValidator.validate(ctx,rows,chunks)
        self.assertFalse(errors,errors)
        self.assertEqual(len(normalized),132)
        self.assertEqual(rows,old)
        cost=next(r for r in normalized if r['metric']=='cost_of_sales' and r['fiscal_year']==2025)
        self.assertEqual(cost['reported_value'],'(15,000)')
        self.assertEqual(Decimal(cost['normalized_value']),Decimal(15_000_000))
        capex=next(r for r in normalized if r['metric']=='capex' and r['fiscal_year']==2025)
        self.assertEqual(Decimal(capex['normalized_value']),Decimal(-5_500_000))
        self.assertEqual(normalize({'reported_value':'12.5','unit':'percent','sign_multiplier':1}),Decimal('.125'))
        self.assertEqual(normalize({'reported_value':'0','unit':'millions','sign_multiplier':1}),0)
        self.assertEqual(normalize({'reported_value':'0.123','unit':'millions','sign_multiplier':1}),123000)

    def test_no_missing_as_zero_or_invalid_sign_guess(self):
        for raw in ('','—','N/A','1.234,56','1e3'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                normalize({'reported_value':raw,'unit':'units','sign_multiplier':1})
        with self.assertRaises(ValueError):
            normalize({'reported_value':'10','unit':'units','sign_multiplier':True})

    def test_all_independent_sample_reconciliations_pass(self):
        ctx,rows,chunks=history_fixture();normalized=LedgerValidator.validate(ctx,rows,chunks)[1]
        checks,derived=LedgerDiagnostics.analyze(normalized,ctx['periods'])
        self.assertEqual(len(checks),18)
        self.assertTrue(all(c['status']=='pass' for c in checks),checks)
        cash=next(d for d in derived if d['fiscal_year']==2025 and d['metric']=='cfo_less_capex')
        self.assertEqual(Decimal(cash['value']),Decimal(9_440_000))
        self.assertIn('NOT automatically unlevered FCFF',cash['note'])
        growth=next(d for d in derived if d['fiscal_year']==2025 and d['metric']=='derived_revenue_growth')
        self.assertAlmostEqual(float(growth['value']),50/45-1)

    def test_missing_components_make_checks_unavailable(self):
        ctx,rows,chunks=history_fixture()
        rows=[r for r in rows if not(r['metric']=='cfo' and r['fiscal_year']==2024)]
        normalized=LedgerValidator.validate(ctx,rows,chunks)[1]
        checks,derived=LedgerDiagnostics.analyze(normalized,ctx['periods'])
        target=next(c for c in checks if c['year']==2024 and c['check'].startswith('Cash change'))
        self.assertEqual(target['status'],'unavailable')
        self.assertNotIn('actual',target)
        self.assertFalse(any(d['metric']=='cfo_less_capex' and d['fiscal_year']==2024 for d in derived))

    def test_duplicates_currency_wrong_quote_and_year_are_blocked(self):
        ctx,rows,chunks=history_fixture()
        dup=deepcopy(rows[0]);dup['record_id']='OTHER'
        self.assertTrue(LedgerValidator.validate(ctx,rows+[dup],chunks)[0])
        for key,value in [('currency','EUR'),('chunk_id','S999'),('quote','Invented quote'),('fiscal_year',2020),('metric','made_up')]:
            edited=deepcopy(rows);edited[0][key]=value
            self.assertTrue(LedgerValidator.validate(ctx,edited,chunks)[0],key)

    def test_conflicts_and_exclusions_require_review(self):
        ctx,rows,chunks=history_fixture();rows[0]['status']='conflicting'
        self.assertTrue(LedgerValidator.validate(ctx,rows,chunks)[0])
        rows[0]['selected']=False
        self.assertTrue(LedgerValidator.validate(ctx,rows,chunks)[0])
        rows[0]['review_note']='Unresolved source conflict; leave FY2023 revenue unknown.'
        errors,normalized=LedgerValidator.validate(ctx,rows,chunks)
        self.assertFalse(errors)
        self.assertFalse(any(r['metric']=='revenue' and r['fiscal_year']==2023 for r in normalized))

    def test_selected_period_bridge_and_explicit_derived_basis(self):
        ctx,rows,chunks=history_fixture();normalized=LedgerValidator.validate(ctx,rows,chunks)[1]
        with self.assertRaisesRegex(ValueError,'Net debt unavailable'):
            LedgerBridge.build(ctx,normalized,2025)
        facts,company=LedgerBridge.build(ctx,normalized,2025,net_debt_basis='debt_less_cash')
        self.assertEqual(company['revenue'],50_000_000)
        self.assertEqual(company['ebitda'],18_500_000)
        self.assertEqual(company['net_debt'],5_000_000)
        self.assertEqual(company['period'],'FY2025')
        self.assertIsNone(company['customer_concentration'])
        net=next(f for f in facts if f['field']=='net_debt')
        self.assertEqual(net['origin'],'derived');self.assertEqual(len(net['dependencies']),3)
        self.assertIsNone(net['quote'])
        _,old=LedgerBridge.build(ctx,normalized,2023,net_debt_basis='debt_less_cash')
        self.assertEqual(old['revenue'],40_000_000)
        self.assertIsNone(old['revenue_growth'])
        facts,_=LedgerBridge.build(ctx,normalized,2025,net_debt_basis='debt_less_cash',use_derived_ebitda=True)
        self.assertEqual(next(f for f in facts if f['field']=='ebitda')['origin'],'derived')

    def test_absent_debt_component_is_not_zero(self):
        ctx,rows,chunks=history_fixture()
        rows=[r for r in rows if not(r['metric']=='short_term_debt' and r['fiscal_year']==2025)]
        normalized=LedgerValidator.validate(ctx,rows,chunks)[1]
        with self.assertRaises(ValueError):LedgerBridge.build(ctx,normalized,2025,net_debt_basis='debt_less_cash')

    def test_rounding_tolerance_respects_disclosed_precision(self):
        ctx,rows,chunks=history_fixture();normalized=LedgerValidator.validate(ctx,rows,chunks)[1]
        for r in normalized:
            if r['fiscal_year']==2025 and r['metric'] in {'total_assets','total_liabilities','total_equity'}:
                r.update(unit='millions',reported_value=f"{Decimal(r['normalized_value'])/1_000_000:.3f}")
        checks,_=LedgerDiagnostics.analyze(normalized,ctx['periods'])
        self.assertTrue(all(c['status']=='pass' for c in checks))
        next(r for r in normalized if r['fiscal_year']==2025 and r['metric']=='total_assets')['normalized_value']='50400000'
        checks,_=LedgerDiagnostics.analyze(normalized,ctx['periods'])
        self.assertEqual(next(c for c in checks if c['year']==2025 and c['check'].startswith('Assets'))['status'],'fail')

    def test_csv_preserves_negative_numbers_and_escapes_formulas(self):
        text=csv_text([{'reported_value':'-2,000','quote':'=HYPERLINK("bad")','review_note':'-SUM(A1)'}])
        self.assertIn('-2,000',text)
        self.assertIn("'=HYPERLINK",text)
        self.assertIn("'-SUM",text)

    def test_ledger_schema_and_statement_extractor(self):
        ctx,rows,chunks=history_fixture()
        self.assertIn('revenue',StatementExtraction.model_json_schema()['$defs']['ReportedRow']['properties']['metric']['enum'])
        class Client:
            def generate(self,prompt,schema,model):
                if schema is LedgerContext:return LedgerContext.model_validate(ctx)
                source=[{k:v for k,v in row.items() if k not in {'record_id','selected','review_note'}} for row in rows if METRICS[row['metric']][0]=='IS']
                return StatementExtraction(rows=source)
        extractor=LedgerExtractor(Client())
        self.assertEqual(len(extractor.discover(chunks,'fake')['periods']),3)
        actual,passages=extractor.statement(chunks,ctx,'IS','fake')
        self.assertTrue(actual);self.assertTrue(passages)


class HistoricalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.extractor=FakeHistoricalExtractor()
        self.factory=lambda:ScreeningWorkflow(FakeExtractor(fixture()[2]),FakeMemoWriter(),ledger_extractor=self.extractor)
        self.service=CaseService(CaseRepository(self.temp.name),self.factory)
        self.rid=self.service.create('Historical Northstar',SOURCE.read_bytes(),SOURCE.name,'fake','Test','Start history',ledger_enabled=True)

    def confirm_history(self,rid=None,rows=None,context=None,year=2025,accept=False):
        rid=rid or self.rid;view=self.service.view(rid)
        return self.service.resume(rid,{'rows':rows or view.state['ledger_rows'],
            'context':context or view.state.get('ledger_context_draft',view.state['ledger_context']),
            'selected_year':year,'reviewer':'Test','note':'Checked source tables, units and annual periods',
            'confirmed':True,'accept_exceptions':accept,'ocr_reviewed':True},view.checkpoint_id)

    def confirm_inputs(self,rid=None):
        rid=rid or self.rid;view=self.service.view(rid)
        return self.service.resume(rid,{'facts':[], 'assumptions':{**ASSUMPTIONS,
            'ledger_basis':{'net_debt':'debt_less_cash','use_derived_ebitda':False}},
            'reviewer':'Test','note':'Reviewed 10x and explicit debt-minus-cash bridge'},view.checkpoint_id)

    def test_history_is_a_real_pause_then_bridge_calculates_from_selected_ledger(self):
        view=self.service.view(self.rid)
        self.assertEqual(view.next,('ledger_review',));self.assertNotIn('calculations',view.state)
        self.assertEqual(self.extractor.calls,['context','IS','BS','CF'])
        view=self.confirm_history()
        self.assertEqual(view.next,('input_review',));self.assertEqual(len(view.state['ledger_confirmed']),132)
        view=self.confirm_inputs()
        self.assertEqual(view.state['company']['revenue'],50_000_000)
        self.assertEqual(view.state['calculations']['enterprise_value'],185_000_000)
        self.assertEqual(view.state['calculations']['equity_value'],180_000_000)
        self.assertEqual(view.next,('memo_review',))

    def test_restart_approval_binding_and_revision_reuses_reviewed_history(self):
        self.confirm_history();view=self.confirm_inputs()
        view=self.service.resume(self.rid,{'decision':'approved','reviewer':'Test','note':'Reviewed memo'},view.checkpoint_id)
        self.assertEqual(view.status,'approved')
        altered=deepcopy(view.state);altered['ledger_confirmed'][0]['normalized_value']='1'
        self.assertEqual(ApprovalPolicy.status(altered),'stale_approval')
        reopened=CaseService(CaseRepository(self.temp.name),self.factory)
        self.assertEqual(reopened.view(self.rid).status,'approved')
        child=reopened.revise(self.rid,'Test','Change history review',view.checkpoint_id)
        self.assertEqual(reopened.view(child).next,('ledger_review',))
        self.assertFalse(reopened.view(child).state['ledger_reviewed'])
        self.assertEqual(self.extractor.calls,['context','IS','BS','CF'])
        self.assertEqual(reopened.view(self.rid).status,'superseded')
        report=report_text(view.state,current_revision_id=child)
        self.assertIn('Historical statement ledger',report)
        self.assertIn('Derived historical metrics',report)

    def test_changed_rows_need_reasons_and_raw_extraction_stays_immutable(self):
        view=self.service.view(self.rid);raw=deepcopy(view.state['ledger_raw']);rows=deepcopy(view.state['ledger_rows'])
        rows[0]['reported_value']='40,001'
        with self.assertRaisesRegex(ValueError,'explain the correction'):
            self.confirm_history(rows=rows)
        rows[0]['review_note']='Illustrative correction verified against source in this software test.'
        view=self.confirm_history(rows=rows,accept=True)
        self.assertEqual(view.state['ledger_raw'],raw)
        event=next(e for e in view.state['audit'] if e['action']=='historical_ledger_confirmed')
        self.assertEqual(len(event['row_changes']),1)

    def test_duplicate_conflict_cannot_be_confirmed(self):
        view=self.service.view(self.rid);rows=deepcopy(view.state['ledger_rows'])
        row=deepcopy(rows[0]);row.update(record_id='CONFLICT',reported_value='41,000',review_note='Unresolved')
        rows.append(row)
        with self.assertRaisesRegex(ValueError,'duplicate selected'):
            self.confirm_history(rows=rows)

    def test_reextract_creates_new_revision_and_repeats_historical_model_stages(self):
        self.confirm_history();view=self.service.view(self.rid)
        child=self.service.revise(self.rid,'Test','Re-extract',view.checkpoint_id,reextract_ledger=True)
        self.assertEqual(self.service.view(child).next,('ledger_review',))
        self.assertEqual(self.extractor.calls,['context','IS','BS','CF']*2)

    def test_partial_statement_failure_is_saved_and_not_filled_with_zeroes(self):
        self.extractor.fail='CF'
        view=self.service.view(self.rid)
        child=self.service.revise(self.rid,'Test','Check outage handling',view.checkpoint_id,reextract_ledger=True)
        state=self.service.view(child).state
        self.assertTrue(state['ledger_errors'])
        self.assertFalse(any(r['metric']=='cfo' for r in state['ledger_raw']))
        with self.assertRaisesRegex(ValueError,'acknowledge'):
            self.confirm_history(child)
        self.confirm_history(child,accept=True)

    def test_legacy_fingerprint_is_unchanged_when_ledger_mode_is_absent(self):
        state={'facts':[],'company':{},'assumptions':{}}
        keys=('case_id','revision_id','document_name','document_hash','pages','facts','company','assumptions','calculations','flags','memo','model_name')
        payload={k:state.get(k) for k in keys};payload['policy_version']=ApprovalPolicy.VERSION
        expected=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        self.assertEqual(ApprovalPolicy.fingerprint(state),expected)
