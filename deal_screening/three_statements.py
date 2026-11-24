"""Simplified linked forecasts, explicit financing, no balancing debt/equity plugs.

Cash taxes equal P&L taxes; opening-debt interest avoids circularity. Other
nonoperating balances are held constant. Separate levered/unlevered loss pools.
"""
import math

OPENING = ('cash', 'receivables', 'inventory', 'other_operating_assets', 'ppe',
           'other_assets', 'payables', 'other_operating_liabilities', 'debt', 'other_liabilities', 'equity')
FINANCING = ('interest_rate', 'debt_issued', 'debt_repaid', 'equity_issued', 'dividends')


def number(value, label, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label}: enter a finite number; missing is not zero.')
    if abs(value) > 1e18 or (not signed and value < 0):
        raise ValueError(f'{label}: outside supported range.')
    return float(value)


def close(a, b):
    return abs(a - b) <= max(.01, max(abs(a), abs(b)) * 1e-10)


class ThreeStatementForecast:
    @staticmethod
    def validate(a, company=None):
        c = a.get('three_statements')
        if not isinstance(c, dict) or not isinstance(c.get('opening'), dict):
            raise ValueError('Provide explicit opening balances for linked statements.')
        if a.get('operating_model') != 'detailed' or a.get('depreciation_model') != 'fixed_assets':
            raise ValueError('Linked statements require detailed forecasts and the fixed-asset schedule.')
        o = c['opening']
        for k in OPENING: number(o.get(k), 'Opening ' + k, signed=k == 'equity')
        assets = sum(o[k] for k in ('cash','receivables','inventory','other_operating_assets','ppe','other_assets'))
        liabilities = sum(o[k] for k in ('payables','other_operating_liabilities','debt','other_liabilities'))
        if not close(assets, liabilities + o['equity']):
            raise ValueError(f'Opening balance sheet does not balance: difference {assets-liabilities-o["equity"]:,.2f}. Correct inputs; no balancing plug is applied.')
        nwc = o['receivables']+o['inventory']+o['other_operating_assets']-o['payables']-o['other_operating_liabilities']
        if not close(nwc,a['opening_nwc']):
            raise ValueError('Opening operating balances do not match opening NWC in the DCF.')
        if not close(o['ppe'],a['fixed_assets']['opening_ppe']):
            raise ValueError('Opening PP&E must match the fixed-asset schedule.')
        if company is not None and not close(o['debt']-o['cash'],company['net_debt']):
            raise ValueError('Opening debt less cash must match the valuation net-debt basis. Reconcile scope before proceeding.')
        rows = c.get('financing')
        if not isinstance(rows,list) or len(rows)!=len(a['forecast']):
            raise ValueError('Provide one financing row for each forecast year.')
        debt = o['debt']
        for i,r in enumerate(rows,1):
            if not isinstance(r,dict) or isinstance(r.get('year'),bool) or r.get('year')!=i:
                raise ValueError('Financing years must be sequential, starting at 1.')
            for k in FINANCING: number(r.get(k),f'Year {i} {k}')
            if r['interest_rate']>1: raise ValueError('Interest rate must be between 0% and 100%.')
            if r['debt_repaid']>debt+r['debt_issued']:
                raise ValueError(f'Year {i}: debt repayment exceeds available debt.')
            debt += r['debt_issued']-r['debt_repaid']

    @classmethod
    def calculate(cls, projections, a, company):
        cls.validate(a,company)
        o = a['three_statements']['opening']
        cash, debt, equity, nol = o['cash'],o['debt'],o['equity'],a['opening_nol']
        income, balance, flow, checks = [],[],[],[]
        any_gap=False
        retained_change=0.
        for r,f in zip(projections,a['three_statements']['financing']):
            year=r['year']; opening_cash=cash; opening_debt=debt; opening_equity=equity
            interest=debt*f['interest_rate']
            ebt=r['ebit']-interest
            used=min(nol,max(ebt,0)*a['nol_utilization_limit'])
            tax=max(ebt-used,0)*a['tax_rate']
            opening_nol=nol; nol=nol-used+max(-ebt,0)
            ni=ebt-tax
            cfo=ni+r['da']-r['change_nwc']; cfi=-r['capex']
            cff=f['debt_issued']-f['debt_repaid']+f['equity_issued']-f['dividends']
            cash+=cfo+cfi+cff
            debt+=f['debt_issued']-f['debt_repaid']
            equity+=ni+f['equity_issued']-f['dividends']
            retained_change+=ni-f['dividends']
            gap=max(-cash,0); any_gap=any_gap or gap>1e-8
            other_assets=max(r['other_nwc'],0); other_liabilities=max(-r['other_nwc'],0)
            # A negative cash diagnostic is NOT presented as a valid cash asset
            # and is not replaced with automatic borrowing.
            valid_cash=cash if gap<=1e-8 else None
            assets=(cash+r['receivables']+r['inventory']+other_assets+r['closing_ppe']+o['other_assets'])
            liabilities=r['payables']+other_liabilities+debt+o['other_liabilities']
            difference=assets-liabilities-equity
            bridge=cfo+cfi-(r['fcff']-interest+(r['cash_taxes']-tax))
            income.append({k:r[k] for k in ('year','revenue','cost_of_sales','rd','sales_marketing','general_admin','other_opex','downside_operating_cost','ebitda','da','ebit')})
            income[-1].update(interest_expense=interest,pretax_income=ebt,tax_expense=tax,net_income=ni,
                              opening_levered_loss_pool=opening_nol,levered_loss_used=used,closing_levered_loss_pool=nol)
            balance.append({'year':year,'cash':valid_cash,'receivables':r['receivables'],'inventory':r['inventory'],
                            'other_operating_assets':other_assets,'net_ppe':r['closing_ppe'],'other_assets_held_constant':o['other_assets'],
                            'total_assets':assets if valid_cash is not None else None,'payables':r['payables'],
                            'other_operating_liabilities':other_liabilities,'debt':debt,'other_liabilities_held_constant':o['other_liabilities'],
                            'total_liabilities':liabilities,'equity':equity,'cumulative_retained_earnings_change':retained_change,'balance_difference':difference if valid_cash is not None else None})
            flow.append({'year':year,'net_income':ni,'depreciation_addback':r['da'],'working_capital_cash_effect':-r['change_nwc'],
                         'cfo':cfo,'cfi':cfi,'debt_issued':f['debt_issued'],'debt_repaid':-f['debt_repaid'],
                         'equity_issued':f['equity_issued'],'dividends':-f['dividends'],'cff':cff,
                         'change_cash':cfo+cfi+cff,'opening_cash_diagnostic':opening_cash,'cash_before_unprovided_financing':cash,
                         'unfunded_shortfall':gap})
            checks.append({'year':year,'status':'funding_shortfall' if gap>1e-8 else 'balanced',
                           'balance_difference':difference if valid_cash is not None else None,
                           'debt_rollforward_difference':debt-opening_debt-f['debt_issued']+f['debt_repaid'],
                           'equity_rollforward_difference':equity-opening_equity-ni-f['equity_issued']+f['dividends'],
                           'cash_rollforward_difference':cash-opening_cash-cfo-cfi-cff,
                           'fcff_to_cash_after_investing_difference':bridge})
            if not close(difference,0) or not close(bridge,0):
                raise ValueError(f'Year {year}: internal statement reconciliation failed.')
        return {'status':'funding_shortfall' if any_gap else 'balanced','income_statement':income,
                'balance_sheet':balance,'cash_flow_statement':flow,'checks':checks,
                'peak_unfunded_shortfall':max(row['unfunded_shortfall'] for row in flow)}
