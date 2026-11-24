"""Data contracts shared by the independent building blocks."""
from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict

FIELDS = {
    "company_name": "company legal name",
    "business": "company business products services description",
    "currency": "reporting currency USD EUR GBP financial statements",
    "period": "financial reporting annual period fiscal year",
    "revenue": "annual revenue sales turnover financial results",
    "ebitda": "reported EBITDA earnings before interest taxes depreciation amortization",
    "net_debt": "net debt net cash borrowings balance sheet year end",
    "revenue_growth": "annual year over year revenue growth percentage",
    "customer_concentration": "largest single customer share revenue concentration percentage",
}
REQUIRED = set(FIELDS) - {"revenue_growth", "customer_concentration"}
NUMERIC = {"revenue", "ebitda", "net_debt", "revenue_growth", "customer_concentration"}
MONEY = {"revenue", "ebitda", "net_debt"}

class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    value: str | None  # Numbers are normalized decimal strings, e.g. "50000000".
    status: Literal["found", "missing", "conflicting"]
    chunk_id: str | None
    quote: str | None
    currency: str | None  # For monetary facts only.
    period: str | None  # Flows: reporting period; net debt: that period's end.
    note: str

class Extraction(BaseModel):
    facts: list[Fact]

class MemoPoint(BaseModel):
    text: str
    references: list[str]

class Memo(BaseModel):
    observations: list[MemoPoint]
    diligence_questions: list[str]

class DealState(TypedDict, total=False):
    ledger_enabled: bool
    reuse_ledger: bool
    ledger_context: dict
    ledger_context_confirmed: dict
    ledger_context_draft: dict
    ledger_raw: list[dict]
    ledger_rows: list[dict]
    ledger_confirmed: list[dict]
    ledger_errors: list[str]
    ledger_retrieval: dict
    ledger_selected_year: int
    ledger_reviewed: bool
    ledger_diagnostics: list[dict]
    ledger_derived: list[dict]
    case_id: str
    revision_id: str
    revision_number: int
    approved_fingerprint: str
    approval_policy_version: str
    reuse_facts: bool
    document_name: str
    document_hash: str
    pages: list[dict]
    chunks: list[dict]
    retrieved: list[dict]
    retrieval: dict
    facts: list[dict]
    extraction_error: str
    issues: list[str]
    company: dict
    assumptions: dict
    calculations: dict
    flags: list[str]
    memo: dict
    memo_error: str
    feedback: str
    decision: str
    audit: list[dict]
    model_name: str
