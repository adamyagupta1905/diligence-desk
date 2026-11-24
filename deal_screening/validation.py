"""Evidence and numerical checks. No model, filesystem, UI or graph dependencies."""
import math
import re
from .models import FIELDS, REQUIRED, NUMERIC, MONEY

def normalize_space(text):
    return " ".join((text or "").split())

def period_matches(field, fact_period, reporting_period):
    """Flows (revenue, EBITDA) must name the exact reporting period.

    Net debt is a balance at the period END, so it may instead give that end
    date, e.g. "31 December 2024" for "year ended 31 December 2024". Only an
    end date written inside the reporting period's own text is accepted; a bare
    label such as "FY2025" gives no date to check, so it still needs an exact match.
    """
    if fact_period == reporting_period:
        return True
    if field != "net_debt" or not fact_period or not reporting_period:
        return False
    end = re.sub(r"^(as at|as of|at) ", "", normalize_space(fact_period).lower())
    period = normalize_space(reporting_period).lower()
    has_date = re.search(r"\d{1,2} [a-z]+ \d{4}|[a-z]+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}", end)
    return bool(has_date) and period.endswith(end)


class InputValidator:
    """Validate extracted or analyst-edited data before calculations."""

    @staticmethod
    def facts(facts, chunks, method="ev_ebitda"):
        """Checks evidence EXISTS, not that it logically proves the extracted number."""
        lookup = {c["id"]: c for c in chunks}
        issues, company = [], {}
        if len(facts) != len(FIELDS) or {f.get("field") for f in facts} != set(FIELDS):
            return ["Exactly one row is required for each field."], {}
        required = REQUIRED - {"ebitda"} if method == "dcf" else REQUIRED
        for fact in facts:
            field = fact["field"]
            if fact.get("status") == "missing" and field not in required:
                company[field] = None
                continue
            if fact.get("status") != "found" or fact.get("value") in (None, ""):
                issues.append(f"{field}: resolve missing/conflicting information.")
                continue
            chunk = lookup.get(fact.get("chunk_id"))
            quote = normalize_space(fact.get("quote"))
            if not chunk or not quote or quote not in normalize_space(chunk["text"]):
                issues.append(f"{field}: supply a supporting quote that exists in the selected passage.")
            value = str(fact["value"]).strip()
            if field in NUMERIC:
                try:
                    if not re.fullmatch(r"-?\d+(\.\d+)?", value):
                        raise ValueError()
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError()
                except (ValueError, OverflowError):
                    issues.append(f"{field}: enter a finite decimal number in full units, without commas.")
                    continue
            company[field] = value
        for field in MONEY:
            fact = next(f for f in facts if f["field"] == field)
            if fact.get("status") == "found":
                if fact.get("currency") != company.get("currency"):
                    issues.append(f"{field}: currency must match the reporting currency.")
                if not period_matches(field, fact.get("period"), company.get("period")):
                    issues.append(f"{field}: confirm the reporting period (net debt is at period end).")
        currency = company.get("currency", "")
        if currency and not re.fullmatch(r"[A-Z]{3}", currency):
            issues.append("currency: use a three-letter uppercase currency code.")
        if company.get("revenue", 1) <= 0:
            issues.append("Revenue must be positive; pre-revenue valuation is outside this model.")
        if method == "ev_ebitda" and company.get("ebitda", 1) <= 0:
            issues.append("Reported EBITDA must be positive for an EBITDA multiple. Choose DCF with reviewed forecasts for losses.")
        concentration = company.get("customer_concentration")
        if concentration is not None and not 0 <= concentration <= 1:
            issues.append("customer_concentration: use a decimal between 0 and 1.")
        growth = company.get("revenue_growth")
        if growth is not None and growth < -1:
            issues.append("revenue_growth: cannot be below -100%.")
        return issues, company

    @staticmethod
    def assumptions(a):
        method = a.get("valuation_method", "ev_ebitda")
        if method == "dcf":
            from .dcf import DCFEngine
            DCFEngine.validate(a)
            return
        if method != "ev_ebitda":
            raise ValueError("Unknown valuation method.")
        for key in ("multiple", "revenue_shock", "margin_shock_pp", "multiple_shock"):
            value = a.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key}: a finite number is required.")
        if a["multiple"] <= 0 or a["multiple"] + a["multiple_shock"] <= 0:
            raise ValueError("Base and stressed valuation multiples must be positive.")
        if a["revenue_shock"] <= -1:
            raise ValueError("The revenue shock must exceed -100%.")
