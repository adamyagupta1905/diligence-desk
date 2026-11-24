"""Deterministic financial calculations. No model, UI or graph dependencies."""
import math
from .validation import InputValidator


class ValuationEngine:
    """EBITDA-multiple valuation and explicit assumption-driven stress scenarios."""

    @staticmethod
    def calculate(company, assumptions):
        InputValidator.assumptions(assumptions)
        if assumptions.get("valuation_method") == "dcf":
            from .dcf import DCFEngine
            return DCFEngine.calculate(company, assumptions)
        revenue, ebitda, debt = (company[k] for k in ("revenue", "ebitda", "net_debt"))
        if revenue <= 0 or ebitda <= 0:
            raise ValueError("Base revenue and EBITDA must be positive.")
        multiple = assumptions["multiple"]
        margin = ebitda / revenue
        ev = ebitda * multiple
        stressed_revenue = revenue * (1 + assumptions["revenue_shock"])
        stressed_margin = margin + assumptions["margin_shock_pp"] / 100
        stressed_ebitda = stressed_revenue * stressed_margin
        stressed_multiple = multiple + assumptions["multiple_shock"]
        # EBITDA multiples are outside this prototype's scope if stress EBITDA <= 0.
        stress_ev = stressed_ebitda * stressed_multiple if stressed_ebitda > 0 else None
        result = {
            "revenue": revenue, "ebitda": ebitda, "net_debt": debt,
            "margin": margin, "enterprise_value": ev, "equity_value": ev - debt,
            "stress_revenue": stressed_revenue, "stress_margin": stressed_margin,
            "stress_ebitda": stressed_ebitda, "stress_multiple": stressed_multiple,
            "stress_enterprise_value": stress_ev,
            "stress_equity_value": stress_ev - debt if stress_ev is not None else None,
            "sensitivity": [
                {"multiple": m, "enterprise_value": ebitda * m, "equity_value": ebitda * m - debt}
                for m in sorted({max(0.1, multiple - 2), multiple, multiple + 2})
            ],
        }
        if not all(math.isfinite(v) for v in result.values() if isinstance(v, (float, int))):
            raise ValueError("Calculation overflow: reduce the input magnitudes.")
        return result

    @staticmethod
    def flags(company, result):
        flags = []
        c = company
        if result.get("margin") is None:
            flags.append("Historical EBITDA is unknown; forecast EBITDA margins are analyst assumptions.")
        elif result["margin"] < 0.20:
            flags.append("EBITDA margin below the illustrative 20% threshold.")
        if c.get("customer_concentration") is None:
            flags.append("Largest-customer concentration is unknown; diligence required.")
        elif c["customer_concentration"] > 0.30:
            flags.append("Largest-customer concentration exceeds the illustrative 30% threshold.")
        if c.get("revenue_growth") is None:
            flags.append("Revenue growth is unknown; diligence required.")
        elif c["revenue_growth"] < 0.10:
            flags.append("Revenue growth below the illustrative 10% threshold.")
        if result["equity_value"] < 0:
            flags.append("The simplified equity bridge produces a negative residual.")
        if result.get("valuation_method") == "dcf":
            if c.get("ebitda") is not None and c["ebitda"] < 0:
                flags.append("Historical EBITDA is negative; the DCF depends on an analyst-assumed recovery.")
            if result["peak_undiscounted_fcff_deficit"] > 0:
                flags.append("Forecast has an operating cash-flow funding deficit; financing terms/dilution are not modeled.")
            if result["terminal_value_share"] is not None and result["terminal_value_share"] > .75:
                flags.append("More than 75% of enterprise value comes from terminal value (illustrative review threshold).")
            if result["remaining_loss_pool"] > 0:
                flags.append("Unused loss-pool tax benefits beyond the forecast are excluded from terminal value.")
            if result.get("three_statements", {}).get("status") == "funding_shortfall":
                flags.append("Linked statement cash shortfall is unfunded; explicit financing is required before approval.")
            if result.get("stress_three_statements", {}).get("status") == "funding_shortfall":
                flags.append("Downside linked statements show an unfunded cash shortfall; the base financing plan does not cover that scenario.")
            if result.get("stress_error"):
                flags.append("DCF stress valuation withheld: " + result["stress_error"])
        elif result["stress_enterprise_value"] is None:
            flags.append("Stress EBITDA is non-positive: EBITDA-multiple stress valuation withheld.")
        return flags
