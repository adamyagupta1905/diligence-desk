"""Year-end unlevered FCFF DCF. Forecast drivers are explicit analyst assumptions.

Cash taxes use a simplified operating-loss pool with a configurable utilization
cap, no refund, no expiry and no interest deduction. This is not a jurisdictional
tax model. Terminal cash flow uses normalized full taxes and g/ROIC reinvestment.
"""
import math
from .operating_forecast import OperatingForecast, COSTS
from .fixed_assets import FixedAssetSchedule
from .three_statements import ThreeStatementForecast


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: enter a finite number.")
    return float(value)


class DCFEngine:
    @staticmethod
    def validate(a):
        mode = a.get("operating_model", "margin")
        if mode not in {"margin", "detailed"}:
            raise ValueError("Unknown operating forecast model.")
        asset_mode = a.get("depreciation_model", "revenue_ratio")
        if asset_mode not in {"revenue_ratio", "fixed_assets"}:
            raise ValueError("Unknown depreciation model.")
        if asset_mode == "fixed_assets":
            if mode != "detailed":
                raise ValueError("Fixed-asset depreciation requires detailed operating forecasts.")
            FixedAssetSchedule.validate(a.get("fixed_assets", {}))
        for key in ("wacc", "terminal_growth", "terminal_roic", "tax_rate", "opening_nwc",
                    "opening_nol", "nol_utilization_limit", "dcf_growth_shock",
                    "dcf_margin_shock", "dcf_wacc_shock"):
            finite(a.get(key), key)
        if not 0 < a["wacc"] <= 1:
            raise ValueError("WACC must be greater than zero and at most 100%.")
        if not 0 <= a["terminal_growth"] < min(a["wacc"], a["terminal_roic"]):
            raise ValueError("Terminal growth must be nonnegative and below both WACC and terminal ROIC.")
        if not 0 < a["terminal_roic"] <= 1:
            raise ValueError("Terminal ROIC must be greater than zero and at most 100%.")
        if not 0 <= a["tax_rate"] < 1 or not 0 <= a["nol_utilization_limit"] <= 1:
            raise ValueError("Tax rate must be 0%-<100%; loss utilization limit must be 0%-100%.")
        if a["opening_nol"] < 0:
            raise ValueError("Opening tax-loss pool cannot be negative.")
        if not 0 <= a["dcf_wacc_shock"] <= 0.5:
            raise ValueError("Downside WACC shock must be between 0 and 50 percentage points.")
        if not -.9 <= a["dcf_growth_shock"] <= 0 or not -1 <= a["dcf_margin_shock"] <= 0:
            raise ValueError("Downside growth/margin shocks must be nonpositive decimal changes.")
        forecast = a.get("forecast")
        if not isinstance(forecast, list) or not 3 <= len(forecast) <= 10:
            raise ValueError("DCF requires 3-10 explicit forecast years.")
        for year, row in enumerate(forecast, 1):
            if not isinstance(row, dict) or isinstance(row.get("year"), bool) or row.get("year") != year:
                raise ValueError("Forecast years must be sequential integers starting at 1.")
            if mode == "detailed":
                OperatingForecast.validate(row)
            keys = ("revenue_growth", "capex_ratio") + (("da_ratio",) if asset_mode == "revenue_ratio" else ()) + (("ebitda_margin", "nwc_ratio") if mode == "margin" else ())
            for key in keys:
                finite(row.get(key), f"Year {year} {key}")
            if not -1 < row["revenue_growth"] <= 3:
                raise ValueError("Annual revenue growth must exceed -100% and be at most 300%.")
            if mode == "margin" and not -5 <= row["ebitda_margin"] <= 1:
                raise ValueError("Forecast EBITDA margins must be between -500% and 100%.")
            if not 0 <= row["capex_ratio"] <= 1 or (asset_mode == "revenue_ratio" and not 0 <= row["da_ratio"] <= 1):
                raise ValueError("D&A and capex ratios must be between 0% and 100% of revenue.")
            if mode == "detailed" and asset_mode == "revenue_ratio" and row["da_ratio"] > sum(row[k] for k in COSTS):
                raise ValueError("D&A cannot exceed total D&A-inclusive operating costs.")
            if mode == "margin" and not -1 <= row["nwc_ratio"] <= 1:
                raise ValueError("Noncash working capital ratio must be between -100% and 100% of revenue.")

        if a.get("linked_statements"):
            ThreeStatementForecast.validate(a)

    @staticmethod
    def value(company, a, *, wacc=None, growth=None, downside=False):
        revenue = finite(company.get("revenue"), "Revenue")
        debt = finite(company.get("net_debt"), "Net debt")
        if revenue <= 0:
            raise ValueError("Revenue must be positive; pre-revenue companies are outside this model.")
        wacc = a["wacc"] if wacc is None else wacc
        growth = a["terminal_growth"] if growth is None else growth
        if wacc <= growth or growth >= a["terminal_roic"] or growth < 0:
            raise ValueError("Discount/growth/ROIC combination is invalid.")
        nwc, nol = a["opening_nwc"], a["opening_nol"]
        assets = FixedAssetSchedule(a["fixed_assets"]) if a.get("depreciation_model") == "fixed_assets" else None
        rows, cumulative, peak_deficit = [], 0., 0.
        for row in a["forecast"]:
            year = row["year"]
            annual_growth = row["revenue_growth"] + (a["dcf_growth_shock"] if downside else 0)
            margin = row.get("ebitda_margin", 0) + (a["dcf_margin_shock"] if downside else 0)
            if annual_growth <= -1:
                raise ValueError("Downside revenue growth would be -100% or lower.")
            revenue *= 1 + annual_growth
            ebitda = revenue * margin
            capex = revenue * row["capex_ratio"]
            asset_row = assets.advance(capex) if assets else {}
            da = asset_row["depreciation"] if assets else revenue * row["da_ratio"]
            ebit = ebitda - da
            schedule = {}
            if a.get("operating_model") == "detailed":
                schedule = OperatingForecast.calculate(revenue, row, a["dcf_margin_shock"] if downside else 0, depreciation=da if assets else None)
                ebit, ebitda = schedule["ebit"], schedule["ebitda"]
                margin = ebitda / revenue
            opening_nol = nol
            used = min(nol, max(ebit, 0) * a["nol_utilization_limit"])
            taxable = max(ebit - used, 0)
            taxes = taxable * a["tax_rate"]
            nol = nol - used + max(-ebit, 0)
            capex = revenue * row["capex_ratio"]
            closing_nwc = schedule["closing_nwc"] if schedule else revenue * row["nwc_ratio"]
            delta_nwc = closing_nwc - nwc
            nwc = closing_nwc
            fcff = ebit - taxes + da - capex - delta_nwc
            discount = (1 + wacc) ** year
            cumulative += fcff
            peak_deficit = max(peak_deficit, -cumulative)
            rows.append({**asset_row, **schedule, "year": year, "revenue": revenue, "revenue_growth": annual_growth,
                         "ebitda_margin": margin, "ebitda": ebitda, "da": da, "ebit": ebit,
                         "opening_loss_pool": opening_nol, "loss_pool_used": used,
                         "closing_loss_pool": nol, "cash_taxes": taxes, "capex": capex,
                         "closing_nwc": nwc, "change_nwc": delta_nwc, "fcff": fcff,
                         "discount_factor": discount, "pv_fcff": fcff / discount})
        last = rows[-1]
        if last["ebit"] <= 0 or last["fcff"] <= 0:
            raise ValueError("Final forecast EBIT and FCFF must be positive before a stable-growth terminal value. Extend/review the forecast; do not force a recovery.")
        terminal_revenue = revenue * (1 + growth)
        terminal_ebit = terminal_revenue * (last["ebit"] / revenue)
        terminal_nopat = terminal_ebit * (1 - a["tax_rate"])
        reinvestment_rate = growth / a["terminal_roic"]
        terminal_fcff = terminal_nopat * (1 - reinvestment_rate)
        terminal_value = terminal_fcff / (wacc - growth)
        pv_terminal = terminal_value / (1 + wacc) ** len(rows)
        pv_forecast = sum(r["pv_fcff"] for r in rows)
        ev = pv_forecast + pv_terminal
        result = {"enterprise_value": ev, "equity_value": ev - debt,
                  "pv_forecast_fcff": pv_forecast, "terminal_value": terminal_value,
                  "pv_terminal_value": pv_terminal,
                  "terminal_value_share": pv_terminal / ev if ev > 0 else None,
                  "terminal_fcff": terminal_fcff, "terminal_revenue": terminal_revenue,
                  "terminal_ebit": terminal_ebit, "terminal_nopat": terminal_nopat,
                  "terminal_reinvestment_rate": reinvestment_rate,
                  "remaining_loss_pool": nol, "peak_undiscounted_fcff_deficit": peak_deficit,
                  "projections": rows}
        numbers = [v for k, v in result.items() if k != "projections" and isinstance(v, (float, int))]
        numbers += [v for row in rows for v in row.values()]
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("DCF overflow: reduce forecast magnitudes.")
        return result

    @classmethod
    def calculate(cls, company, assumptions):
        cls.validate(assumptions)
        a = assumptions
        if a.get("linked_statements"):
            ThreeStatementForecast.validate(a, company)
        result = cls.value(company, a)
        if a.get("linked_statements"):
            result["three_statements"] = ThreeStatementForecast.calculate(result["projections"], a, company)
        ebitda = company.get("ebitda")
        if ebitda is not None:
            finite(ebitda, "Historical EBITDA")
        result.update(depreciation_model=a.get("depreciation_model", "revenue_ratio"), operating_model=a.get("operating_model", "margin"), valuation_method="dcf", revenue=company["revenue"], ebitda=ebitda,
                      net_debt=company["net_debt"],
                      margin=ebitda / company["revenue"] if ebitda is not None else None)
        try:
            stress = cls.value(company, a, wacc=a["wacc"] + a["dcf_wacc_shock"], downside=True)
            if a.get("linked_statements"):
                result["stress_three_statements"] = ThreeStatementForecast.calculate(stress["projections"], a, company)
            result.update(stress_enterprise_value=stress["enterprise_value"],
                          stress_equity_value=stress["equity_value"], stress_projections=stress["projections"],
                          stress_error="")
        except ValueError as exc:
            result.update(stress_enterprise_value=None, stress_equity_value=None,
                          stress_projections=[], stress_error=str(exc))
        sensitivity = []
        for wacc in [max(.001, a["wacc"] - .01), a["wacc"], a["wacc"] + .01]:
            for growth in sorted({max(0., a["terminal_growth"] - .005), a["terminal_growth"], a["terminal_growth"] + .005}):
                try:
                    value = cls.value(company, a, wacc=wacc, growth=growth)
                    sensitivity.append({"wacc": wacc, "terminal_growth": growth,
                                        "enterprise_value": value["enterprise_value"], "equity_value": value["equity_value"], "error": ""})
                except ValueError as exc:
                    sensitivity.append({"wacc": wacc, "terminal_growth": growth,
                                        "enterprise_value": None, "equity_value": None, "error": str(exc)})
        result["sensitivity"] = sensitivity
        return result


def default_dcf(company):
    """UI-only illustrative template; the engine never invents missing drivers."""
    loss = company.get("ebitda") is not None and company["ebitda"] < 0
    margins = [-.10, -.02, .08, .15, .20] if loss else [.16, .18, .20, .21, .22]
    return {"valuation_method": "dcf", "wacc": .12, "terminal_growth": .025,
            "terminal_roic": .15, "tax_rate": .25,
            "opening_nwc": company.get("revenue", 0) * .10, "opening_nol": 0.,
            "nol_utilization_limit": 1., "dcf_growth_shock": -.05,
            "dcf_margin_shock": -.03, "dcf_wacc_shock": .02,
            "forecast": [{"year": i + 1, "revenue_growth": growth, "ebitda_margin": margins[i],
                          "da_ratio": .03, "capex_ratio": .04, "nwc_ratio": .10}
                         for i, growth in enumerate([.12, .10, .08, .06, .04])]}
