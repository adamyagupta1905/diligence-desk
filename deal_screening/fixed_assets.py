"""Aggregate opening PP&E plus annual straight-line capex cohorts, zero residuals.

A forecasting convention, not a statutory asset register. No disposals, impairment,
FX, revaluation, leases, land, construction-in-progress or intangible amortization.
"""
import math


class FixedAssetSchedule:
    @staticmethod
    def validate(config):
        if not isinstance(config, dict):
            raise ValueError("Provide a fixed-asset configuration.")
        for key in ('opening_ppe', 'opening_remaining_life', 'new_asset_life', 'first_year_fraction'):
            v = config.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f'Fixed assets {key}: enter a finite number.')
        if not 0 <= config['opening_ppe'] <= 1e18:
            raise ValueError('Opening depreciable net PP&E must be nonnegative and at most 1e18.')
        for key in ('opening_remaining_life', 'new_asset_life'):
            if not 1 <= config[key] <= 100:
                raise ValueError('Asset lives must be between 1 and 100 years.')
        if config['first_year_fraction'] not in (0., .5, 1.):
            raise ValueError('Select next-year, half-year or full-year additions depreciation.')

    def __init__(self, config):
        self.validate(config)
        self.config = dict(config)
        self.balance = float(config['opening_ppe'])
        self.cohorts = [{'remaining': self.balance,
                         'annual': self.balance / config['opening_remaining_life'], 'year': 0}]
        self.year = 0

    def advance(self, capex):
        if isinstance(capex, bool) or not isinstance(capex, (int, float)) or not math.isfinite(capex) or capex < 0:
            raise ValueError('PP&E capex must be a finite nonnegative amount.')
        self.year += 1
        opening = self.balance
        self.cohorts.append({'remaining': capex, 'annual': capex / self.config['new_asset_life'], 'year': self.year})
        depreciation, old_charge, new_charge = 0., 0., 0.
        for cohort in self.cohorts:
            fraction = self.config['first_year_fraction'] if cohort['year'] == self.year else 1.
            charge = min(cohort['remaining'], cohort['annual'] * fraction)
            cohort['remaining'] -= charge
            depreciation += charge
            if cohort['year'] == self.year: new_charge += charge
            else: old_charge += charge
        self.balance = sum(c['remaining'] for c in self.cohorts)
        return {'opening_ppe': opening, 'ppe_additions': capex,
                'depreciation_existing_cohorts': old_charge, 'depreciation_current_additions': new_charge,
                'depreciation': depreciation, 'closing_ppe': self.balance,
                'ppe_rollforward_difference': opening + capex - depreciation - self.balance}
