"""Independent cohort arithmetic, DCF integration and invalid-input controls."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening.fixed_assets import FixedAssetSchedule
from deal_screening.dcf import DCFEngine
from deal_screening.operating_forecast import detailed_defaults
from test_advanced import flat_assumptions

CONFIG={'opening_ppe':100.,'opening_remaining_life':5.,'new_asset_life':5.,'first_year_fraction':.5}

def assumptions():
    a=flat_assumptions();a.update(operating_model='detailed',depreciation_model='fixed_assets',fixed_assets=dict(CONFIG))
    a['fixed_assets']['opening_ppe']=10.
    a['forecast']=detailed_defaults(a['forecast'])
    for row in a['forecast']:
        row.update(cost_of_sales_ratio=.4,rd_ratio=.1,sales_marketing_ratio=.1,general_admin_ratio=.15,other_opex_ratio=.05,
                   capex_ratio=.1,receivable_days=0.,inventory_days=0.,payable_days=0.,other_nwc_ratio=0.)
        row.pop('da_ratio');row.pop('ebitda_margin');row.pop('nwc_ratio')
    return a

class FixedAssetTests(unittest.TestCase):
    def test_hand_calculated_half_year_cohorts_roll_forward(self):
        s=FixedAssetSchedule(CONFIG)
        r=s.advance(20)
        self.assertEqual(r['depreciation'],22) # opening100/5 +new20/5 *0.5
        self.assertEqual(r['closing_ppe'],98)
        r=s.advance(20)
        self.assertEqual(r['opening_ppe'],98)
        self.assertEqual(r['depreciation'],26) #20+4+2
        self.assertEqual(r['closing_ppe'],92)
        self.assertAlmostEqual(r['ppe_rollforward_difference'],0)

    def test_full_next_year_and_final_partial_charges(self):
        for fraction,first in [(0.,0.),(.5,5.),(1.,10.)]:
            s=FixedAssetSchedule({**CONFIG,'opening_ppe':0.,'new_asset_life':2.,'first_year_fraction':fraction})
            rows=[s.advance(20)]+[s.advance(0) for _ in range(5)]
            self.assertEqual(rows[0]['depreciation'],first)
            self.assertEqual(sum(r['depreciation'] for r in rows),20)
            self.assertEqual(rows[-1]['closing_ppe'],0)
            self.assertTrue(all(r['closing_ppe']>=0 for r in rows))

    def test_opening_pool_exhausts_without_new_spending(self):
        s=FixedAssetSchedule({**CONFIG,'opening_remaining_life':2.5})
        rows=[s.advance(0) for _ in range(4)]
        self.assertEqual([r['depreciation'] for r in rows],[40,40,20,0])
        self.assertEqual(rows[-1]['closing_ppe'],0)

    def test_dcf_uses_depreciation_once_and_charges_capex_once(self):
        a=assumptions();before=deepcopy(a)
        c=DCFEngine.calculate({'revenue':100.,'ebitda':20.,'net_debt':5.},a)
        r=c['projections'][0]
        self.assertAlmostEqual(r['depreciation'],3) # opening10/5 +new10/5*.5
        self.assertAlmostEqual(r['closing_ppe'],17)
        self.assertAlmostEqual(r['ebitda'],20)
        self.assertAlmostEqual(r['ebit'],17)
        self.assertAlmostEqual(r['cash_taxes'],4.25)
        self.assertAlmostEqual(r['fcff'],5.75) #17-4.25+3-10
        self.assertAlmostEqual(r['total_operating_costs'],83)
        self.assertEqual(a,before)
        self.assertEqual(c['depreciation_model'],'fixed_assets')

    def test_downside_has_independent_asset_cohorts_and_sensitivity_is_repeatable(self):
        a=assumptions();a.update(dcf_growth_shock=-.1,dcf_margin_shock=-.02)
        company={'revenue':100.,'ebitda':20.,'net_debt':0.}
        c=DCFEngine.calculate(company,a);r=c['stress_projections'][0]
        self.assertAlmostEqual(r['ppe_additions'],9)
        self.assertAlmostEqual(r['depreciation'],2.9)
        self.assertAlmostEqual(r['ebit'],13.3) #90*20%-1.8-2.9
        self.assertEqual(c,DCFEngine.calculate(company,a))

    def test_invalid_configuration_and_incompatible_mode_block(self):
        for key,value in [('opening_ppe',-1),('opening_remaining_life',0),('new_asset_life',float('nan')),('first_year_fraction',.3),('new_asset_life',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):FixedAssetSchedule({**CONFIG,key:value})
        for value in (None,[]):
            with self.assertRaises(ValueError):FixedAssetSchedule.validate(value)
        s=FixedAssetSchedule(CONFIG)
        for capex in (-1,True,float('inf')):
            with self.assertRaises(ValueError):s.advance(capex)
        with self.assertRaises(ValueError):DCFEngine.validate({**assumptions(),'operating_model':'margin'})

    def test_zero_asset_costs_can_produce_loss_then_recover(self):
        a=assumptions();a['fixed_assets'].update(opening_ppe=100.,opening_remaining_life=1.)
        r=DCFEngine.calculate({'revenue':100.,'ebitda':20.,'net_debt':0.},a)['projections']
        self.assertLess(r[0]['ebit'],0)
        self.assertEqual(r[0]['cash_taxes'],0)
        self.assertGreater(r[-1]['ebit'],0)
