"""Deterministic detailed operating schedules. Costs include D&A; no double charge."""
import math

COSTS = ("cost_of_sales_ratio", "rd_ratio", "sales_marketing_ratio", "general_admin_ratio", "other_opex_ratio")
DAYS = ("receivable_days", "inventory_days", "payable_days")


class OperatingForecast:
    @staticmethod
    def validate(row):
        for key in (*COSTS, *DAYS, "other_nwc_ratio"):
            v = row.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"Detailed forecast {key}: enter a finite number.")
            lower, upper = (-1, 1) if key == "other_nwc_ratio" else ((0, 730) if key in DAYS else (0, 5))
            if not lower <= v <= upper:
                raise ValueError(f"Detailed forecast {key}: outside supported range {lower} to {upper}.")

    @staticmethod
    def calculate(revenue, row, margin_shock=0., *, depreciation=None):
        costs = {key.removesuffix("_ratio"): revenue * row[key] for key in COSTS}
        # A negative margin shock is an additional operating expense, not a
        # second independent margin forecast overriding the detailed costs.
        downside_cost = -revenue * margin_shock
        total = sum(costs.values()) + downside_cost
        ebit = revenue - total
        if depreciation is None:
            da = revenue * row["da_ratio"]
        else:
            # In asset-linked mode entered operating costs EXCLUDE D&A.
            # The schedule supplies the sole depreciation expense.
            da = depreciation
            ebit -= da
        ar = revenue * row["receivable_days"] / 365
        inventory = costs["cost_of_sales"] * row["inventory_days"] / 365
        ap = costs["cost_of_sales"] * row["payable_days"] / 365
        other = revenue * row["other_nwc_ratio"]
        profit_fields = {"gross_profit": revenue - costs["cost_of_sales"]} if depreciation is None else {
            "gross_profit_before_da": revenue - costs["cost_of_sales"],
            "operating_costs_before_da": total}
        return {**costs, **profit_fields, "downside_operating_cost": downside_cost,
                "total_operating_costs": total + (da if depreciation is not None else 0),
                "ebit": ebit, "da": da, "ebitda": ebit + da,
                "receivables": ar, "inventory": inventory, "payables": ap,
                "other_nwc": other, "closing_nwc": ar + inventory - ap + other}


def detailed_defaults(forecast):
    """Illustrative assumptions only; never inferred from absent historical lines."""
    return [{**r, "cost_of_sales_ratio": .35, "rd_ratio": .10,
             "sales_marketing_ratio": .12, "general_admin_ratio": .20,
             "other_opex_ratio": .03, "receivable_days": 45.,
             "inventory_days": 10., "payable_days": 30., "other_nwc_ratio": .02}
            for r in forecast]
