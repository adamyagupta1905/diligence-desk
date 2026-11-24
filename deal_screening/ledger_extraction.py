"""Bounded, statement-specific retrieval and structured historical extraction."""
import json
from .documents import PassageRetriever
from .ledger import METRICS, LedgerContext, StatementExtraction, candidate_rows


class LedgerExtractor:
    def __init__(self, client, retriever=None):
        self.client = client
        # Any object with retrieve(chunks, queries=..., top_k=...): BM25 or hybrid.
        self.retriever = retriever if retriever is not None else PassageRetriever(top_k=3)

    def discover(self, chunks, model_name):
        prompt = """Identify the consolidated company and up to five most recent COMPLETED ANNUAL
fiscal periods explicitly represented in this document. Never treat quarterly,
forecast, segment or budget columns as historical annual statements. Fiscal year
labels need not match calendar years. Only supply end_date if explicitly given.
Cite exact existing quotes and passage IDs for company, business, currency and
periods. Missing business description may use value='', chunk_id=null, quote=null.
Do not invent years, dates, currency or financial facts. A table header can evidence
several fiscal years. Include one period per fiscal year. PASSAGES:\n""" + json.dumps(chunks)
        context = self.client.generate(prompt, LedgerContext, model_name).model_dump()
        years = [p["fiscal_year"] for p in context["periods"]]
        if not 1 <= len(years) <= 5 or len(years) != len(set(years)):
            raise ValueError("Invalid discovered annual periods")
        return context

    def statement(self, chunks, context, statement, model_name):
        metrics = {key: meta[1] for key,meta in METRICS.items() if meta[0] == statement}
        queries = list(metrics.values()) + ["reporting currency amounts thousands millions fiscal years annual consolidated"]
        passages = self.retriever.retrieve(chunks, queries=queries, top_k=3)
        context_ids = {item.get("chunk_id") for item in context.values() if isinstance(item,dict)}
        context_ids |= {p["chunk_id"] for p in context["periods"]}
        selected = {c["id"] for c in passages} | context_ids
        passages = [c for c in chunks if c["id"] in selected]
        prompt = """Extract the supplied historical statement's consolidated ANNUAL line items
for the listed fiscal years only. Use only the allowed metric codes. Return only
explicit found values and unresolved conflicting candidates; OMIT undisclosed
metrics. Do not fill gaps with zero or derive amounts. Dashes are not zero unless
a source note explicitly defines them that way. Return conflicting alternatives
as separate status='conflicting' records; do not choose between them.
reported_value preserves the numeric figure as printed (including parentheses,
minus signs or comma grouping), WITHOUT multiplying thousands/millions. Set unit
and currency from the source. sign_multiplier is explicitly 1 or -1 to standardize
presentation: income-statement expense amounts are positive deductions, while
cash-flow capex/debt repayment/buybacks/dividends are negative outflows. Preserve
actual losses and tax benefits. Never use abs() or infer a sign without context.
Include a verbatim quote for the value/row and, where needed, a separate exact
context_quote/context_chunk_id for the table's fiscal-year/currency/unit heading.
Use reported_label exactly as the source describes the item. Do not mix adjusted
with reported EBITDA, liabilities with interest-bearing debt, cash including
restricted amounts with plain cash, or total noncash adjustments with working
capital. Put scope/ambiguities in note; do not silently remap unsupported metrics.
If OCR digits/units are ambiguous, omit or flag conflicting, never guess.
ALLOWED METRICS:\n""" + json.dumps(metrics) + "\nCONTEXT:\n" + json.dumps(context)
        prompt += "\nPASSAGES:\n" + json.dumps(passages)
        result = self.client.generate(prompt, StatementExtraction, model_name)
        return candidate_rows([row.model_dump() for row in result.rows], statement), [c["id"] for c in passages]
