"""Independent linked-accounting examples and review controls, no live API."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deal_screening.dcf import DCFEngine
from deal_screening.three_statements import ThreeStatementForecast
from deal_screening.workflow import ScreeningWorkflow
from deal_screening.cases import CaseService
from deal_screening.storage import CaseRepository
from helpers import FakeExtractor, FakeMemoWriter, fixture
from test_fixed_assets import assumptions


def linked_assumptions():
    a=assumptions();a['linked_statements']=True;a['opening_nwc']=13.
    for r in a['forecast']:
        r.update(receivable_days=36.5,inventory_days=36.5,payable_days=18.25,other_nwc_ratio=.01)
    a['three_statements']={'opening':{'cash':10.,'receivables':10.,'inventory':4.,'other_operating_assets':1.,
        'ppe':10.,'other_assets':5.,'payables':2.,'other_operating_liabilities':0.,'debt':20.,'other_liabilities':3.,'equity':15.},
        'financing':[{'year':i,'interest_rate':.1,'debt_issued':5. if i==1 else 0.,
                      'debt_repaid':4. if i==1 else 0.,'equity_issued':3. if i==1 else 0.,'dividends':2. if i==1 else 0.} for i in (1,2,3)]}
    return a

COMPANY={'revenue':100.,'ebitda':20.,'net_debt':10.}

class LinkedStatementTests(unittest.TestCase):
    def test_independent_first_year_all_statement_links_and_tax_bridge(self):
        a=linked_assumptions();c=DCFEngine.calculate(COMPANY,a);s=c['three_statements']
        self.assertEqual(s['status'],'balanced')
        i,b,f=s['income_statement'][0],s['balance_sheet'][0],s['cash_flow_statement'][0]
        expected={'ebitda':20.,'da':3.,'ebit':17.,'interest_expense':2.,'pretax_income':15.,'tax_expense':3.75,'net_income':11.25}
        for k,v in expected.items():self.assertAlmostEqual(i[k],v)
        for k,v in {'cfo':14.25,'cfi':-10.,'cff':2.,'change_cash':6.25,'cash_before_unprovided_financing':16.25}.items():self.assertAlmostEqual(f[k],v)
        for k,v in {'cash':16.25,'debt':21.,'equity':27.25,'total_assets':53.25,'total_liabilities':26.,'net_ppe':17.}.items():self.assertAlmostEqual(b[k],v)
        self.assertAlmostEqual(c['projections'][0]['fcff'],5.75)
        self.assertAlmostEqual(s['income_statement'][1]['interest_expense'],2.1)
        for r in s['checks']:
            for k,v in r.items():
                if k.endswith('difference'):self.assertAlmostEqual(v,0.)

    def test_financing_does_not_change_unlevered_enterprise_value(self):
        a=linked_assumptions();before=deepcopy(a);c=DCFEngine.calculate(COMPANY,a)
        plain=deepcopy(a);plain.pop('linked_statements');plain.pop('three_statements')
        self.assertEqual(c['enterprise_value'],DCFEngine.calculate(COMPANY,plain)['enterprise_value'])
        a['three_statements']['financing'][0]['equity_issued']=20.
        changed=DCFEngine.calculate(COMPANY,a)
        self.assertEqual(c['enterprise_value'],changed['enterprise_value'])
        self.assertAlmostEqual(changed['three_statements']['balance_sheet'][0]['cash']-c['three_statements']['balance_sheet'][0]['cash'],17.)
        self.assertEqual(before['three_statements']['opening'],a['three_statements']['opening'])

    def test_unbalanced_opening_missing_and_incompatible_scopes_block(self):
        for key,value in [('equity',14.),('cash',None),('debt',-1.),('other_assets',float('nan'))]:
            a=linked_assumptions();a['three_statements']['opening'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):DCFEngine.calculate(COMPANY,a)
        for key,value in [('opening_nwc',12.),('operating_model','margin'),('depreciation_model','revenue_ratio')]:
            a=linked_assumptions();a[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):DCFEngine.calculate(COMPANY,a)
        with self.assertRaisesRegex(ValueError,'net-debt'):DCFEngine.calculate({**COMPANY,'net_debt':11},linked_assumptions())
        a=linked_assumptions();a['fixed_assets']['opening_ppe']=11
        with self.assertRaisesRegex(ValueError,'PP&E'):DCFEngine.calculate(COMPANY,a)

    def test_financing_limits_and_unknowns_block(self):
        for key,value in [('debt_repaid',30.),('interest_rate',True),('interest_rate',1.2),('dividends',None),('equity_issued',-1.)]:
            a=linked_assumptions();a['three_statements']['financing'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):DCFEngine.calculate(COMPANY,a)
        a=linked_assumptions();a['three_statements']['financing'].pop()
        with self.assertRaises(ValueError):DCFEngine.calculate(COMPANY,a)

    def test_funding_shortfall_withholds_cash_asset_without_debt_plug(self):
        a=linked_assumptions();a['three_statements']['financing'][0]['dividends']=100.
        c=DCFEngine.calculate(COMPANY,a);s=c['three_statements']
        self.assertEqual(s['status'],'funding_shortfall')
        self.assertAlmostEqual(s['cash_flow_statement'][0]['unfunded_shortfall'],81.75)
        self.assertIsNone(s['balance_sheet'][0]['cash'])
        self.assertIsNone(s['balance_sheet'][0]['total_assets'])
        self.assertEqual(s['balance_sheet'][0]['debt'],21.)
        self.assertGreater(c['enterprise_value'],0)
        workflow=ScreeningWorkflow(FakeExtractor(fixture()[2]),FakeMemoWriter())
        state={'assumptions':a,'calculations':c,'memo_error':'','memo':{'observations':[{}]}}
        with self.assertRaisesRegex(ValueError,'shortfall'):
            workflow.prepare_memo_review(state,{'decision':'approved','reviewer':'Test','note':'Checked'})

    def test_levered_losses_separate_from_operating_tax_pool(self):
        a=linked_assumptions();a['three_statements']['financing'][0]['interest_rate']=1.
        c=DCFEngine.calculate(COMPANY,a);s=c['three_statements']
        self.assertAlmostEqual(s['income_statement'][0]['net_income'],-3.)
        self.assertAlmostEqual(s['income_statement'][0]['closing_levered_loss_pool'],3.)
        self.assertEqual(c['projections'][0]['closing_loss_pool'],0.)
        self.assertAlmostEqual(c['projections'][0]['cash_taxes'],4.25)

    def test_negative_other_nwc_and_opening_operating_liabilities(self):
        a=linked_assumptions();o=a['three_statements']['opening']
        o.update(other_operating_assets=0.,other_operating_liabilities=1.,equity=13.)
        a['opening_nwc']=11.
        for r in a['forecast']:r['other_nwc_ratio']=-.01
        s=DCFEngine.calculate(COMPANY,a)['three_statements']
        self.assertEqual(s['status'],'balanced')
        self.assertAlmostEqual(s['balance_sheet'][0]['other_operating_liabilities'],1.)
        self.assertEqual(s['balance_sheet'][0]['other_operating_assets'],0.)

    def test_downside_separate_opening_balances_and_repeatability(self):
        a=linked_assumptions();a['dcf_growth_shock']=-.1
        before=deepcopy(a);c=DCFEngine.calculate(COMPANY,a)
        self.assertEqual(c['stress_three_statements']['cash_flow_statement'][0]['opening_cash_diagnostic'],10.)
        self.assertEqual(c['stress_three_statements']['income_statement'][0]['interest_expense'],2.)
        self.assertNotEqual(c['three_statements']['balance_sheet'][0]['cash'],c['stress_three_statements']['balance_sheet'][0]['cash'])
        self.assertEqual(a,before)
        self.assertEqual(c,DCFEngine.calculate(COMPANY,a))

    def test_case_restart_invalid_submission_recovery_and_approval_binding(self):
        import tempfile
        from deal_screening.approvals import ApprovalPolicy
        from helpers import ROOT
        with tempfile.TemporaryDirectory() as directory:
            make=lambda:CaseService(CaseRepository(directory),lambda:ScreeningWorkflow(FakeExtractor(fixture()[2]),FakeMemoWriter()))
            service=make();rid=service.create('Linked case',(ROOT/'sample_documents/01_complete_millions.txt').read_bytes(),'fictional.txt','fake','Test','Test')
            a=linked_assumptions();a['opening_nwc']=5_000_000.;a['fixed_assets']['opening_ppe']=10_000_000.
            a['three_statements']['opening']=dict(cash=5_000_000.,receivables=5_000_000.,inventory=2_000_000.,other_operating_assets=1_000_000.,
                 ppe=10_000_000.,other_assets=10_000_000.,payables=2_000_000.,other_operating_liabilities=1_000_000.,debt=17_000_000.,other_liabilities=3_000_000.,equity=10_000_000.)
            for r in a['three_statements']['financing']:
                for k in ('debt_issued','debt_repaid','equity_issued','dividends'):r[k]=0.
            v=service.view(rid);answer={'facts':v.state['facts'],'assumptions':a,'reviewer':'Test','note':'Checked'}
            bad=deepcopy(answer);bad['assumptions']['three_statements']['opening']['equity']=1.
            with self.assertRaises(ValueError):service.resume(rid,bad,v.checkpoint_id)
            v=service.resume(rid,answer,v.checkpoint_id)
            self.assertEqual(v.state['calculations']['three_statements']['status'],'balanced')
            service=make();v=service.view(rid)
            approved=service.resume(rid,{'decision':'approved','reviewer':'Test','note':'Checked'},v.checkpoint_id)
            self.assertEqual(make().view(rid).status,'approved')
            changed=deepcopy(approved.state);changed['assumptions']['three_statements']['financing'][0]['equity_issued']=10.
            self.assertEqual(ApprovalPolicy.status(changed),'stale_approval')
            child=service.revise(rid,'Test','New financing assumptions',approved.checkpoint_id)
            self.assertEqual(service.view(child).next,('input_review',))
            self.assertEqual(service.view(child).status,'pending')
