"""Detailed DCF independent accounting checks and model-switch compatibility."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening.dcf import DCFEngine
from deal_screening.operating_forecast import OperatingForecast, detailed_defaults
from test_advanced import flat_assumptions


class DetailedDCFTests(unittest.TestCase):
    def setUp(self):
        self.a = flat_assumptions()
        self.a['operating_model'] = 'detailed'
        self.a['forecast'] = detailed_defaults(self.a['forecast'])
        for r in self.a['forecast']:
            r.update(cost_of_sales_ratio=.4, rd_ratio=.1, sales_marketing_ratio=.1,
                     general_admin_ratio=.15, other_opex_ratio=.05, da_ratio=.02,
                     capex_ratio=.03, receivable_days=36.5, inventory_days=36.5,
                     payable_days=18.25, other_nwc_ratio=.01)
            r.pop('ebitda_margin');r.pop('nwc_ratio')
        self.company = {'revenue':100.,'ebitda':22.,'net_debt':10.}
        self.a['opening_nwc'] = 13.

    def test_independent_income_working_capital_and_fcff_bridge(self):
        c=DCFEngine.calculate(self.company,self.a);r=c['projections'][0]
        # Expenses 40+10+10+15+5=80 INCLUDING D&A of 2.
        self.assertAlmostEqual(r['ebit'],20)
        self.assertAlmostEqual(r['ebitda'],22)
        self.assertAlmostEqual(r['gross_profit'],60)
        self.assertAlmostEqual(r['receivables'],10)
        self.assertAlmostEqual(r['inventory'],4)
        self.assertAlmostEqual(r['payables'],2)
        self.assertAlmostEqual(r['closing_nwc'],13)
        self.assertAlmostEqual(r['change_nwc'],0)
        self.assertAlmostEqual(r['fcff'],14) #20-5+2-3
        expected=sum(14/1.1**y for y in (1,2,3))+150/1.1**3
        self.assertAlmostEqual(c['enterprise_value'],expected)
        self.assertAlmostEqual(c['equity_value'],expected-10)

    def test_working_capital_release_is_positive_cash_flow(self):
        self.a['opening_nwc']=20
        r=DCFEngine.calculate(self.company,self.a)['projections'][0]
        self.assertAlmostEqual(r['change_nwc'],-7)
        self.assertAlmostEqual(r['fcff'],21)

    def test_downside_expense_shock_and_lower_sales_feed_every_schedule(self):
        self.a.update(dcf_margin_shock=-.03,dcf_growth_shock=-.05)
        r=DCFEngine.calculate(self.company,self.a)['stress_projections'][0]
        self.assertAlmostEqual(r['revenue'],95)
        self.assertAlmostEqual(r['cost_of_sales'],38)
        self.assertAlmostEqual(r['downside_operating_cost'],2.85)
        self.assertAlmostEqual(r['ebit'],16.15)
        self.assertAlmostEqual(r['closing_nwc'],12.35)

    def test_losses_are_retained_then_loss_pool_is_used(self):
        self.a['forecast'][0]['other_opex_ratio']=.45
        c=DCFEngine.calculate(self.company,self.a)
        self.assertAlmostEqual(c['projections'][0]['ebit'],-20)
        self.assertAlmostEqual(c['projections'][0]['cash_taxes'],0)
        self.assertAlmostEqual(c['projections'][1]['loss_pool_used'],20)
        self.assertLess(c['projections'][0]['fcff'],0)

    def test_missing_nonfinite_negative_costs_and_days_rejected(self):
        for key,value in [('rd_ratio',None),('receivable_days',float('nan')),
                          ('inventory_days',-1),('cost_of_sales_ratio',-.1),
                          ('payable_days',True),('other_nwc_ratio',2),('da_ratio',.9)]:
            a=deepcopy(self.a);a['forecast'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):DCFEngine.calculate(self.company,a)
        with self.assertRaises(ValueError):DCFEngine.calculate(self.company,{**self.a,'operating_model':'unknown'})

    def test_total_costs_can_exceed_sales_but_terminal_must_recover(self):
        for r in self.a['forecast']:r['other_opex_ratio']=1.
        with self.assertRaisesRegex(ValueError,'Final forecast'):DCFEngine.calculate(self.company,self.a)

    def test_legacy_margin_mode_unchanged_and_inputs_not_mutated(self):
        before=deepcopy(self.a);DCFEngine.calculate(self.company,self.a)
        self.assertEqual(self.a,before)
        c=DCFEngine.calculate(self.company,flat_assumptions())
        self.assertAlmostEqual(c['enterprise_value'],150)
