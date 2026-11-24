"""Two cohesive AI tasks: extract evidence-backed facts; draft a referenced memo."""
import json
from .models import FIELDS, Extraction, Memo
from .llm import ModelClient

def missing_fact(field, note="Not established from retrieved passages."):
    return {"field": field, "value": None, "status": "missing", "chunk_id": None,
            "quote": None, "currency": None, "period": None, "note": note}

def allowed_references(state):
    refs = {f["chunk_id"] for f in state["facts"] if f["status"] == "found" and f.get("chunk_id")}
    for row in state.get("ledger_confirmed",[]):
        refs.add("LEDGER:"+row["record_id"])
        refs.update(value for value in (row.get("chunk_id"),row.get("context_chunk_id")) if value)
    refs |= {"DERIVED:"+str(d["fiscal_year"])+":"+d["metric"] for d in state.get("ledger_derived",[])}
    refs |= {"CALC:" + k for k, v in state["calculations"].items() if v is not None}
    refs |= {"ASSUMPTION:" + k for k in state["assumptions"]}
    refs.add("RULES")
    return refs

class FactExtractor:
    def __init__(self, client: ModelClient):
        self.client = client

    def extract(self, passages, model_name):
        prompt = """Extract exactly one fact for each listed field from the retrieved passages.
    Return missing values as null, never zero. For conflicts use status conflicting,
    set value to null, and explain the alternatives in note; do not silently choose.
    Choose a single latest
    completed annual reporting period. Use reported EBITDA, not adjusted EBITDA.
    Every found fact needs a verbatim supporting quote and its chunk_id. The quote
    must support the value, unit and relevant period. Do not infer net debt from total
    debt. Money values must be normalized to full currency units (50 million ->
    50000000); ratios to decimals (18% -> 0.18). Other values remain text.
    Use ISO currency codes. For monetary facts set currency and period; net debt must
    be at the reporting period end. For revenue and EBITDA, period must exactly match
    the chosen period fact. If currency or period is unclear, mark missing/conflicting.
    Negative reported EBITDA is valid historical evidence: preserve its minus sign.
    If OCR leaves an ambiguous digit/sign/unit, mark the fact missing or conflicting
    instead of silently replacing characters or inventing a normalized value.
    Do not extract a valuation multiple: that is an analyst assumption.
    FIELDS:\n""" + json.dumps(FIELDS) + "\nPASSAGES:\n" + json.dumps(passages)
        result = self.client.generate(prompt, Extraction, model_name)
        facts = [f.model_dump() for f in result.facts]
        if len(facts) != len(FIELDS) or {f["field"] for f in facts} != set(FIELDS):
            raise ValueError("Unexpected or duplicate extraction fields")
        return facts


class MemoWriter:
    def __init__(self, client: ModelClient):
        self.client = client

    def draft(self, state):
        payload = {k: state[k] for k in (
            "company", "facts", "assumptions", "calculations", "flags", "feedback")}
        if state.get("ledger_enabled"):
            payload["historical_context"]=state.get("ledger_context_confirmed")
            payload["historical_ledger"]=state.get("ledger_confirmed",[])
            payload["historical_derived"]=state.get("ledger_derived",[])
            payload["historical_checks"]=state.get("ledger_diagnostics",[])
            payload["selected_historical_year"]=state.get("ledger_selected_year")
        payload["source_passages"] = [c for c in state["chunks"]
                                      if c["id"] in allowed_references(state)]
        prompt = """Draft a concise preliminary deal-screening memo, not an investment decision.
    Ledger records use LEDGER:record_id references; derived historical values use
    DERIVED:year:metric references. Distinguish reported values, analyst corrections,
    derived historical metrics and forecast assumptions. Historical CFO less capex
    is not automatically unlevered FCFF. Do not describe derived EBITDA/net debt or
    derived growth as reported. Explain material failed/unavailable reconciliations
    and coverage limitations without pretending they were fixed.
    Return 3-6 observation points, each with supporting references from the supplied
    allowed reference IDs, plus 3-5 unanswered diligence questions. Cite document facts
    with Sxxx; Python results with CALC:key; assumptions with ASSUMPTION:key; rule
    flags with RULES. Keep document facts, assumptions and calculations distinct.
    When valuation_method is dcf, explain forecast drivers, cash flows, discounting,
    terminal-value dependence and operating funding deficits using supplied Python
    outputs. If depreciation_model is fixed_assets, cost buckets EXCLUDE D&A:
    EBITDA = revenue less these costs; EBIT deducts scheduled PP&E depreciation.
    Closing PP&E = opening PP&E + additions - depreciation. No intangible
    amortization is modelled. The cohort schedule ends at the explicit forecast;
    terminal value uses normalized ROIC reinvestment, not perpetual cohort runoff.
    Otherwise in detailed operating mode, cost categories INCLUDE D&A: EBIT is
    revenue less costs; EBITDA adds D&A once. Do not call these analyst forecast
    costs reported historical facts or describe the schedules as a full three-statement model.
    Forecasts, tax-loss treatment, WACC and terminal ROIC are assumptions,
    not document facts. Negative early cash flows are not removed. The operating
    funding deficit is not a financing plan and is already reflected in FCFF; do
    not subtract it a second time from enterprise value. OCR evidence may contain
    recognition errors even after review.
    When linked statements are present, explain how net income rolls book equity,
    CFO plus investing/financing moves cash, and debt follows explicit borrowing
    and repayments. Interest and net-income taxes are separate from unlevered DCF
    taxes; do not replace FCFF with levered CFO. Book equity is not DCF equity value.
    An unfunded shortfall is unresolved financing, not negative available cash,
    automatic debt or proof that a balance sheet is valid. A balanced simplified
    forecast does not imply complete accounting coverage or investment approval.
    Do not perform new financial calculations, invent facts, describe the selected
    multiple as market evidence, or imply approval. Thresholds are illustrative rules.
    Never treat an unknown value as zero. A historical customer share and a future
    contract expiry are separate facts: do not project that share through the expiry.
    Do not assume an operational cause for a low margin; ask neutral questions instead. Include sensitivity/stress limitations.
    Analyst feedback requests revisions but is not new factual evidence.
    ALLOWED REFERENCES:\n""" + json.dumps(sorted(allowed_references(state)))
        prompt += "\nDATA:\n" + json.dumps(payload)
        memo = self.client.generate(prompt, Memo, state["model_name"]).model_dump()
        if not 3 <= len(memo["observations"]) <= 6:
            raise ValueError("Unexpected memo length")
        for point in memo["observations"]:
            if not point["text"].strip() or not point["references"]:
                raise ValueError("Memo observations must include references")
            if not set(point["references"]) <= allowed_references(state):
                raise ValueError("Unknown memo reference")
        return memo
