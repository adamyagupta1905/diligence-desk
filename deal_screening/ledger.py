"""Historical statement contracts, normalization, diagnostics and valuation bridge.

Reported rows remain separate from analyst-confirmed rows and derived checks.
No balancing plugs, FX conversion, or missing-as-zero substitutions are performed.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Metric -> (statement, display label, monetary versus decimal ratio).
METRICS = {
    "revenue": ("IS", "Revenue", "money"),
    "cost_of_sales": ("IS", "Cost of sales", "money"),
    "gross_profit": ("IS", "Gross profit", "money"),
    "research_development": ("IS", "Research and development", "money"),
    "sales_marketing": ("IS", "Sales and marketing", "money"),
    "general_admin": ("IS", "General and administrative", "money"),
    "other_operating_expenses": ("IS", "Other operating expenses", "money"),
    "total_operating_expenses": ("IS", "Total operating expenses", "money"),
    "operating_income": ("IS", "Operating income / EBIT", "money"),
    "interest_expense": ("IS", "Interest expense", "money"),
    "tax_expense": ("IS", "Income tax expense", "money"),
    "net_income": ("IS", "Net income", "money"),
    "ebitda": ("IS", "Reported EBITDA", "money"),
    "revenue_growth": ("IS", "Reported revenue growth", "ratio"),
    "customer_concentration": ("IS", "Reported largest-customer concentration", "ratio"),
    "cash": ("BS", "Cash and cash equivalents", "money"),
    "marketable_securities": ("BS", "Marketable securities", "money"),
    "receivables": ("BS", "Trade receivables", "money"),
    "inventory": ("BS", "Inventory", "money"),
    "other_current_assets": ("BS", "Other current assets", "money"),
    "ppe": ("BS", "Net property, plant and equipment", "money"),
    "other_noncurrent_assets": ("BS", "Other noncurrent assets", "money"),
    "total_assets": ("BS", "Total assets", "money"),
    "payables": ("BS", "Trade payables", "money"),
    "other_current_liabilities": ("BS", "Other current liabilities", "money"),
    "short_term_debt": ("BS", "Short-term interest-bearing debt", "money"),
    "long_term_debt": ("BS", "Long-term interest-bearing debt", "money"),
    "other_noncurrent_liabilities": ("BS", "Other noncurrent liabilities", "money"),
    "total_liabilities": ("BS", "Total liabilities", "money"),
    "total_equity": ("BS", "Total equity", "money"),
    "net_debt": ("BS", "Reported net debt", "money"),
    "da": ("CF", "Depreciation and amortization", "money"),
    "sbc": ("CF", "Share-based compensation", "money"),
    "deferred_tax": ("CF", "Deferred-tax operating adjustment", "money"),
    "change_operating_nwc": ("CF", "Signed operating working-capital cash effect", "money"),
    "cfo": ("CF", "Net operating cash flow", "money"),
    "capex": ("CF", "Capital-expenditure cash flow (negative outflow)", "money"),
    "cfi": ("CF", "Net investing cash flow", "money"),
    "debt_issued": ("CF", "Debt issuance cash flow", "money"),
    "debt_repaid": ("CF", "Debt repayment cash flow (negative outflow)", "money"),
    "buybacks": ("CF", "Share repurchase cash flow (negative outflow)", "money"),
    "dividends": ("CF", "Dividend cash flow (negative outflow)", "money"),
    "cff": ("CF", "Net financing cash flow", "money"),
    "fx_effect": ("CF", "FX effect on cash", "money"),
    "cash_change": ("CF", "Net change in cash", "money"),
    "cash_open": ("CF", "Opening cash per cash-flow statement", "money"),
    "cash_close": ("CF", "Closing cash per cash-flow statement", "money"),
}


class CitedText(BaseModel):
    value: str
    chunk_id: str | None
    quote: str | None


class AnnualPeriod(BaseModel):
    fiscal_year: int
    label: str
    end_date: str | None
    chunk_id: str
    quote: str


class LedgerContext(BaseModel):
    company_name: CitedText
    business: CitedText
    reporting_currency: CitedText
    periods: list[AnnualPeriod]


class ReportedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str = Field(json_schema_extra={"enum":list(METRICS)})
    reported_label: str
    fiscal_year: int
    reported_value: str | None
    currency: str | None
    unit: Literal["units", "thousands", "millions", "percent", "decimal"]
    sign_multiplier: int  # Explicit presentation conversion, exactly 1 or -1.
    status: Literal["found", "missing", "conflicting"]
    chunk_id: str | None
    quote: str | None
    context_chunk_id: str | None  # Optional table headings / units / year evidence.
    context_quote: str | None
    note: str


class StatementExtraction(BaseModel):
    rows: list[ReportedRow]


def evidence_error(chunk_id, quote, chunks):
    lookup = {c["id"]: c["text"] for c in chunks}
    text = " ".join((quote or "").split())
    return not text or chunk_id not in lookup or text not in " ".join(lookup[chunk_id].split())


def normalize(row):
    """Return an exact Decimal in base currency units or decimal-ratio units."""
    text = str(row.get("reported_value") or "").strip().replace("\u2212", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        raise ValueError("Use an explicit signed number; dashes/blanks are unknown, not zero.")
    try:
        value = Decimal(text.replace(",", ""))
        multiplier = row.get("sign_multiplier")
        if type(multiplier) is not int or multiplier not in (-1, 1):
            raise ValueError("sign_multiplier must be 1 or -1.")
        unit = row.get("unit")
        scale = {"units": 1, "thousands": 1000, "millions": 1_000_000,
                 "percent": Decimal("0.01"), "decimal": 1}.get(unit)
        if scale is None:
            raise ValueError("Unknown unit.")
        value = value * Decimal(scale) * multiplier
        if not value.is_finite() or abs(value) > Decimal("1e18"):
            raise ValueError("Normalized amount exceeds the ledger's finite 1e18 bound.")
        return value
    except InvalidOperation as exc:
        raise ValueError("Invalid numerical value.") from exc


class LedgerValidator:
    @staticmethod
    def validate(context, rows, chunks):
        errors = []
        try:
            context = LedgerContext.model_validate(context).model_dump()
        except Exception:
            return ["Historical context is incomplete or malformed."], []
        for field in ("company_name", "business", "reporting_currency"):
            fact = context[field]
            if field == "business" and not fact["value"].strip() and not fact["quote"]:
                continue
            if not fact["value"].strip() or evidence_error(fact["chunk_id"], fact["quote"], chunks):
                errors.append(f"{field}: provide text and an existing supporting quote.")
        if not re.fullmatch(r"[A-Z]{3}", context["reporting_currency"]["value"]):
            errors.append("Reporting currency must be an uppercase three-letter code.")
        periods = context["periods"]
        years = [p["fiscal_year"] for p in periods]
        if not 1 <= len(periods) <= 5 or len(years) != len(set(years)):
            errors.append("Provide 1-5 distinct annual fiscal years; never invent missing years.")
        for period in periods:
            if not 1900 <= period["fiscal_year"] <= 2200 or not period["label"].strip():
                errors.append("Invalid annual period label/year.")
            if evidence_error(period["chunk_id"], period["quote"], chunks):
                errors.append(f"FY{period['fiscal_year']}: period evidence is missing.")
            if period["end_date"]:
                try:
                    date.fromisoformat(period["end_date"])
                except ValueError:
                    errors.append("Period end dates must use YYYY-MM-DD or remain blank when not disclosed.")
        normalized, keys, ids, currencies = [], set(), set(), {}
        if len(rows) > 400:
            return errors + ["Use at most 400 ledger candidates per revision."], []
        for row in rows:
            rid = row.get("record_id", "")
            if not rid or rid in ids:
                errors.append("Each candidate needs a unique stable record ID.")
            ids.add(rid)
            if not row.get("selected", True):
                if not str(row.get("review_note") or "").strip():
                    errors.append(f"{rid}: explain why this source candidate is excluded.")
                continue
            metric = row.get("metric")
            if metric not in METRICS:
                errors.append(f"{rid}: choose a supported metric code."); continue
            if row.get("fiscal_year") not in years or type(row.get("fiscal_year")) is not int:
                errors.append(f"{rid}: choose an evidenced annual fiscal year."); continue
            if row.get("status") != "found":
                errors.append(f"{rid}: resolve or explicitly exclude missing/conflicting candidates."); continue
            key = (row["fiscal_year"], metric)
            if key in keys:
                errors.append(f"{rid}: duplicate selected {metric} for FY{key[0]}; resolve the conflict.")
            keys.add(key)
            if evidence_error(row.get("chunk_id"), row.get("quote"), chunks):
                errors.append(f"{rid}: value quote must exist in its referenced passage.")
            if row.get("context_chunk_id") or row.get("context_quote"):
                if evidence_error(row.get("context_chunk_id"), row.get("context_quote"), chunks):
                    errors.append(f"{rid}: context/heading quote must exist.")
            kind = METRICS[metric][2]
            if kind == "money":
                if row.get("unit") not in {"units", "thousands", "millions"}:
                    errors.append(f"{rid}: monetary metrics need a currency scale.")
                if not re.fullmatch(r"[A-Z]{3}", str(row.get("currency") or "")):
                    errors.append(f"{rid}: monetary metrics need a currency code.")
                currencies.setdefault(row["fiscal_year"], set()).add(row.get("currency"))
            elif row.get("unit") not in {"percent", "decimal"}:
                errors.append(f"{rid}: ratio metrics need percent or decimal units.")
            try:
                value = normalize(row)
                if metric == "customer_concentration" and not 0 <= value <= 1:
                    raise ValueError("Customer concentration must be between 0 and 1.")
                if metric == "revenue_growth" and value < -1:
                    raise ValueError("Revenue growth cannot be below -100%.")
                normalized.append({**deepcopy(row), "statement": METRICS[metric][0],
                                   "normalized_value": format(value, "f"),
                                   "origin": "reported" if not row.get("review_note") else "analyst_reviewed"})
            except ValueError as exc:
                errors.append(f"{rid}: {exc}")
        for year, values in currencies.items():
            if len(values) > 1:
                errors.append(f"FY{year}: mixed selected currencies; choose one presentation, no implicit FX conversion.")
        if not normalized:
            errors.append("Select at least one evidenced historical line item.")
        return errors, normalized


class LedgerDiagnostics:
    @staticmethod
    def analyze(rows, periods):
        lookup = {(r["fiscal_year"], r["metric"]): r for r in rows}
        checks, derived = [], []
        def get(year, metric):
            return lookup.get((year, metric))
        def value(row):
            return Decimal(row["normalized_value"])
        def check(year, name, target, terms):
            required = [target] + [metric for metric, coefficient in terms]
            records = [get(year, key) for key in required]
            if any(row is None for row in records):
                checks.append({"year": year, "check": name, "status": "unavailable",
                               "note": "Missing: " + ", ".join(k for k, row in zip(required,records) if row is None)})
                return
            expected = sum((value(get(year,key)) * factor for key,factor in terms), Decimal(0))
            actual = value(get(year,target)); delta = actual - expected
            # A rounding allowance based on the least precise source scale.
            def precision(row):
                raw=str(row["reported_value"]).replace(",","").strip("() +-")
                decimals=len(raw.split(".")[1]) if "." in raw else 0
                return Decimal({"units":1,"thousands":1000,"millions":1_000_000}[row["unit"]]) * Decimal(10) ** -decimals
            quantum = max(precision(r) for r in records)
            tolerance = quantum * Decimal("0.5") * len(records)
            checks.append({"year": year, "check": name, "status": "pass" if abs(delta) <= tolerance else "fail",
                           "actual": str(actual), "expected": str(expected), "difference": str(delta),
                           "tolerance": str(tolerance), "dependencies": [r["record_id"] for r in records]})
        def derive(year, metric, inputs, operation, note):
            records = [get(year, key) for key in inputs]
            if all(records):
                result = operation(*[value(r) for r in records])
                if result is not None:
                    derived.append({"fiscal_year": year, "metric": metric, "value": str(result),
                                    "currency": records[0].get("currency"), "origin": "derived",
                                    "dependencies": [r["record_id"] for r in records], "note": note})
        for period in periods:
            year = period["fiscal_year"]
            check(year,"Assets = liabilities + equity","total_assets",[("total_liabilities",1),("total_equity",1)])
            check(year,"Gross profit = revenue - cost of sales","gross_profit",[("revenue",1),("cost_of_sales",-1)])
            check(year,"Operating income = gross profit - total OpEx","operating_income",[("gross_profit",1),("total_operating_expenses",-1)])
            check(year,"Reported EBITDA = operating income + D&A (scope must match)","ebitda",[("operating_income",1),("da",1)])
            check(year,"Cash change = CFO + CFI + CFF + FX","cash_change",[("cfo",1),("cfi",1),("cff",1),("fx_effect",1)])
            check(year,"Closing cash = opening cash + cash change","cash_close",[("cash_open",1),("cash_change",1)])
            derive(year,"derived_ebitda",["operating_income","da"],lambda a,b:a+b,
                   "Operating income plus disclosed D&A; verify scope before using as valuation EBITDA.")
            derive(year,"derived_net_debt",["short_term_debt","long_term_debt","cash"],lambda a,b,c:a+b-c,
                   "Interest-bearing short/long-term debt minus stated cash; excludes other debt-like claims/restricted-cash adjustments.")
            derive(year,"cfo_less_capex",["cfo","capex"],lambda a,b:a+b,
                   "Operating cash flow plus signed capex. This is NOT automatically unlevered FCFF.")
            derive(year,"ebitda_margin",["ebitda","revenue"],lambda a,b:a/b if b else None,
                   "Derived from reported EBITDA and revenue.")
        ordered = sorted(p["fiscal_year"] for p in periods)
        for previous,year in zip(ordered,ordered[1:]):
            a,b=get(previous,"revenue"),get(year,"revenue")
            if a and b and year == previous+1 and a["currency"] == b["currency"] and value(a)>0:
                derived.append({"fiscal_year":year,"metric":"derived_revenue_growth","value":str(value(b)/value(a)-1),
                                "currency":None,"origin":"derived","dependencies":[a["record_id"],b["record_id"]],
                                "note":"Consecutive fiscal-year reported revenue; check comparable period length and accounting scope."})
        return checks, derived


class LedgerBridge:
    @staticmethod
    def build(context, rows, year, *, net_debt_basis="reported", use_derived_ebitda=False):
        selected = {r["metric"]:r for r in rows if r["fiscal_year"] == year}
        period = next((p for p in context["periods"] if p["fiscal_year"] == year), None)
        if period is None:
            raise ValueError("Select an annual period present in the confirmed ledger.")
        checks, derived = LedgerDiagnostics.analyze(rows,context["periods"])
        derived = {d["metric"]:d for d in derived if d["fiscal_year"] == year}
        facts, company = [], {}
        for key, source_key in [("company_name","company_name"),("business","business"),("currency","reporting_currency")]:
            item=context[source_key]
            company[key]=item["value"]
            facts.append({"field":key,"value":item["value"],"status":"found","chunk_id":item["chunk_id"],
                          "quote":item["quote"],"currency":None,"period":None,"note":"Confirmed historical context","origin":"reported"})
        if not company["business"]:
            company["business"] = "Business description not established in the supplied statements."
            facts[1].update(value=None,status="missing",origin="unknown")
        company["period"]=period["label"]
        facts.append({"field":"period","value":period["label"],"status":"found","chunk_id":period["chunk_id"],
                      "quote":period["quote"],"currency":None,"period":period["label"],"note":"Selected annual period","origin":"reported"})
        monetary = [r for r in selected.values() if METRICS[r["metric"]][2] == "money"]
        if monetary:
            company["currency"]=monetary[0]["currency"]
            # Currency uses the selected period's evidenced monetary row.
            facts[2].update(value=company["currency"],chunk_id=monetary[0]["chunk_id"],quote=monetary[0]["quote"])
        for metric in ("revenue","ebitda","net_debt","revenue_growth","customer_concentration"):
            row = selected.get(metric)
            calculation = None
            if metric == "net_debt" and net_debt_basis == "debt_less_cash":
                row=None; calculation=derived.get("derived_net_debt")
            elif metric == "net_debt" and net_debt_basis != "reported":
                raise ValueError("Unknown net-debt basis.")
            if metric == "ebitda" and use_derived_ebitda:
                row=None; calculation=derived.get("derived_ebitda")
            if metric == "revenue_growth" and row is None:
                calculation=derived.get("derived_revenue_growth")
            if row:
                number=row["normalized_value"]
                fact={"field":metric,"value":number,"status":"found","chunk_id":row["chunk_id"],"quote":row["quote"],
                      "currency":row["currency"],"period":period["label"],"note":"Confirmed ledger "+row["record_id"],
                      "origin":"reported","dependencies":[row["record_id"]]}
            elif calculation:
                number=calculation["value"]
                fact={"field":metric,"value":number,"status":"found","chunk_id":None,"quote":None,
                      "currency":company["currency"],"period":period["label"],"note":calculation["note"],
                      "origin":"derived","dependencies":calculation["dependencies"]}
            else:
                number=None
                fact={"field":metric,"value":None,"status":"missing","chunk_id":None,"quote":None,
                      "currency":None,"period":period["label"],"note":"Not established in confirmed ledger","origin":"unknown"}
            company[metric]=float(Decimal(number)) if number is not None else None
            facts.append(fact)
        if company["revenue"] is None or company["revenue"] <= 0:
            raise ValueError("Selected period needs evidenced positive revenue before valuation.")
        if company["net_debt"] is None:
            raise ValueError("Net debt unavailable: supply reported net debt, or select debt less cash with all three components evidenced (including explicit zeroes).")
        return facts, company


def candidate_rows(rows, statement):
    result=[]
    for index,row in enumerate(rows,1):
        row=deepcopy(row)
        if row.get("metric") not in METRICS or METRICS[row["metric"]][0] != statement:
            raise ValueError("Statement extraction contains an unsupported or misclassified metric.")
        digest=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:12]
        row.update(record_id=f"{statement}-{index:03d}-{digest}",selected=True,review_note="")
        result.append(row)
    return result
