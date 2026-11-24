"""Pure report rendering; usable independently of Streamlit or Gemini."""
import json
from datetime import datetime, timezone
from .approvals import ApprovalPolicy

LIMITATIONS = (
    "Fictional/public PDFs or UTF-8 text only; no external verification. Local English OCR "
    "is available for scanned pages (10 OCR pages, 30 total pages, 10 MB limit). OCR "
    "may misread signs, units and tables; word scores do not prove correctness. "
    "Search combines BM25 keywords with Gemini embeddings (falling back to keywords "
    "alone if embeddings fail, as recorded in the audit trail); retrieval can still miss evidence. "
    "Quote matching and valid references do not prove semantic or numerical correctness. "
    "An analyst must check units, periods, reported EBITDA, conflicts, and memo claims. "
    "Historical ledgers support up to five annual fiscal years and a controlled metric taxonomy. "
    "Reconciliations test arithmetic and displayed rounding tolerance, not audit assurance; "
    "missing components make checks unavailable. Derived net debt/EBITDA retain source dependencies "
    "and require explicit input-basis review. No balancing plugs or implicit FX conversion. "
    "EBITDA multiples require positive EBITDA. Enterprise DCF supports losses with explicit "
    "3-10 year forecasts and positive final-year EBIT/FCFF. Forecasts, WACC, terminal growth, "
    "ROIC, tax-loss treatment, multiples and shocks are reviewed assumptions, not market evidence. "
    "Detailed revenue-ratio mode uses D&A-inclusive costs; fixed-asset mode uses D&A-exclusive "
    "cost buckets and scheduled PP&E depreciation. Fixed assets use zero residual values and "
    "reviewed remaining/new lives and addition-year timing; no disposals, impairment, FX, land, "
    "CIP, leases or intangible amortization. Terminal reinvestment remains normalized g/ROIC. "
    "Detailed forecasts use a 365-day working-capital convention; "
    "inventory and payable days use cost of sales. Historical scopes require analyst mapping. "
    "Optional linked statements cover a simplified reviewed opening balance sheet, interest, "
    "profit, operating balances, PP&E, cash, debt and equity. Other nonoperating balances stay "
    "constant. Interest uses opening debt; P&L taxes equal cash taxes with a separate levered "
    "loss pool. No automatic financing, deferred taxes, OCI, buybacks, amortization or full "
    "statutory disclosures. Unfunded cash prevents linked-statement approval. "
    "DCF uses year-end unlevered FCFF, a constant WACC and stable-growth terminal value with "
    "g/ROIC reinvestment. Loss carryforwards are a simplified operating-loss pool: no refunds, "
    "expiry, interest deductions or jurisdiction-specific rules; terminal taxes are normalized "
    "and unused terminal loss benefits excluded. Equity = EV minus net debt only; financing "
    "terms, dilution, distress/default probability and other transaction adjustments are excluded. "
    "The cash-flow deficit proxy is not a funding plan. Pre-revenue firms, banks and insurers "
    "need other models. Stress holds net debt fixed. "
    "Rule thresholds are illustrative. Approval uses a self-entered name, not authenticated "
    "identity or a digital signature. Local SQLite storage is not encrypted or tamper-proof. "
    "Exported files are historical snapshots: verify current case status in the application."
)

def report_text(state, *, current_revision_id=None):
    current = current_revision_id is None or current_revision_id == state.get("revision_id")
    status = ApprovalPolicy.status(state, current)
    lines = ["# General Deal-Screening Report", f"Status: {status}",
             f"Recorded review decision: {state.get('decision', 'pending')}",
             f"Approval policy: {state.get('approval_policy_version', 'not yet approved')}",
             f"Case: {state.get('case_id', 'standalone')} | Revision: {state.get('revision_number', 'standalone')}",
             f"Revision ID: {state.get('revision_id', 'standalone')}",
             f"Current revision at export: {current_revision_id or state.get('revision_id', 'standalone')}",
             f"Status assessed at UTC: {datetime.now(timezone.utc).isoformat()}",
             "Historical exports do not certify that this remains the current approved revision.",
             f"Document: {state['document_name']}", f"SHA256: {state['document_hash']}",
             f"Model: {state['model_name']}",
             "Approval means analyst sign-off on this screening memo, not investment authorization.",
             "\n## Confirmed facts"]
    for fact in state.get("facts", []):
        lines.append(f"- {fact['field']}: {fact['value']} [{fact['status']}; source={fact.get('chunk_id')}; origin={fact.get('origin','reported')}; dependencies={fact.get('dependencies',[])}]")
    lines += ["\n## Document extraction provenance", json.dumps([
        {"page": p["page"], "method": p.get("extraction_method", "text/native"),
         "ocr_confidence": p.get("ocr_confidence")} for p in state.get("pages", [])], indent=2)]
    if state.get("ledger_enabled"):
        lines += ["\n## Historical statement ledger", "Selected valuation fiscal year: "+str(state.get("ledger_selected_year")),
                  "Confirmed rows preserve reported values, units and signs alongside Python-normalized amounts. Missing figures are not zero.",
                  json.dumps(state.get("ledger_confirmed",[]),indent=2),
                  "\n## Historical reconciliations",json.dumps(state.get("ledger_diagnostics",[]),indent=2),
                  "\n## Derived historical metrics — not source-reported facts",json.dumps(state.get("ledger_derived",[]),indent=2),
                  "\n## Historical extraction coverage issues",json.dumps(state.get("ledger_errors",[]),indent=2)]
    for title, key in [("Analyst assumptions", "assumptions"),
                       ("Python calculations", "calculations"), ("Rule flags", "flags")]:
        lines += [f"\n## {title}", json.dumps(state.get(key, {}), indent=2)]
    lines += ["\n## LLM memo — analyst review required"]
    for point in state.get("memo", {}).get("observations", []):
        lines.append("- " + point["text"] + " [" + ", ".join(point["references"]) + "]")
    for question in state.get("memo", {}).get("diligence_questions", []):
        lines.append("- Question: " + question)
    lines += [state.get("memo_error", ""), "\n## Evidence"]
    used = {f["chunk_id"] for f in state.get("facts", []) if f["status"] == "found"}
    for chunk in state.get("chunks", []):
        if chunk["id"] in used:
            lines += [f"### {chunk['id']} — page {chunk['page']}", chunk["text"]]
    lines += ["\n## Limitations", LIMITATIONS, "\n## Audit trail", json.dumps(state["audit"], indent=2)]
    return "\n\n".join(lines)
