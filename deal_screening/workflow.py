"""LangGraph coordinates components; it does not implement their business logic."""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .ai import missing_fact
from .audit import AuditLog
from .approvals import ApprovalPolicy
from .documents import DocumentReader, PassageRetriever
from .retrieval import describe
from .finance import ValuationEngine
from .llm import safe_api_error
from .models import DealState, FIELDS
from .validation import InputValidator
from .ledger import LedgerBridge
from .ledger_steps import HistoricalLedgerSteps


class ScreeningWorkflow:
    """Inject collaborators to swap models or test components independently.

    extractor and memo_writer need only implement extract() and draft(). A fake
    implementation can replace either without modifying this graph or the UI.
    """

    def __init__(self, extractor, memo_writer, retriever=None, validator=None,
                 engine=None, audit=None, ledger_extractor=None):
        self.extractor = extractor
        self.memo_writer = memo_writer
        self.retriever = retriever if retriever is not None else PassageRetriever()
        self.validator = validator if validator is not None else InputValidator()
        self.engine = engine if engine is not None else ValuationEngine()
        self.audit = audit if audit is not None else AuditLog()
        self.ledger = HistoricalLedgerSteps(ledger_extractor,self.audit)

    def record(self, state, action, **details):
        return self.audit.append(state.get("audit", []), action, **details)

    def retrieve(self, state):
        chunks = DocumentReader.chunks(state["pages"])
        passages = self.retriever.retrieve(chunks)
        run = describe(self.retriever)
        return {"chunks": chunks, "retrieved": passages, "retrieval": run,
                "audit": self.record(state, "retrieved", total_chunks=len(chunks),
                                      retrieved_chunks=len(passages), **run)}

    def extract(self, state):
        if state.get("ledger_enabled"):
            return {"facts":[missing_fact(field) for field in FIELDS],"extraction_error":"",
                    "audit":self.record(state,"core_inputs_deferred_to_historical_ledger",
                                        note="Historical mode selects valuation inputs after ledger confirmation; no separate nine-fact API call.")}
        error = ""
        try:
            facts = self.extractor.extract(state["retrieved"], state["model_name"])
        except Exception as exc:
            error = f"Extraction unavailable ({safe_api_error(exc)}). No extracted facts were substituted."
            facts = [missing_fact(field) for field in FIELDS]
        return {"facts": facts, "extraction_error": error,
                "audit": self.record(state, "extraction", success=not bool(error), error=error)}

    def validate(self, state):
        issues, _ = self.validator.facts(state["facts"], state["chunks"])
        return {"issues": issues}

    def input_review(self, state):
        answer = interrupt({"stage": "input_review", "issues": state["issues"]})
        return self.prepare_input_review(state,answer)

    def prepare_input_review(self,state,answer):
        method = answer["assumptions"].get("valuation_method", "ev_ebitda")
        if state.get("ledger_enabled"):
            if not state.get("ledger_reviewed"):
                raise ValueError("Confirm the historical statement ledger first.")
            basis=answer["assumptions"].get("ledger_basis",{})
            facts,company=LedgerBridge.build(state["ledger_context_confirmed"],state["ledger_confirmed"],
                state["ledger_selected_year"],net_debt_basis=basis.get("net_debt","reported"),
                use_derived_ebitda=bool(basis.get("use_derived_ebitda")))
            issues=[]
            if method == "ev_ebitda" and (company["ebitda"] is None or company["ebitda"] <= 0):
                issues.append("The multiple method needs positive evidenced EBITDA; use DCF or explicitly select a supported derived EBITDA basis.")
        else:
            facts = answer["facts"]
            issues, company = self.validator.facts(facts, state["chunks"], method=method)
        has_ocr = any(p.get("extraction_method") == "ocr" for p in state["pages"])
        if has_ocr and not answer.get("ocr_reviewed"):
            raise ValueError("Confirm the original scanned pages against OCR text before calculating.")
        if issues:
            raise ValueError("Inputs still need correction: " + "; ".join(issues))
        self.validator.assumptions(answer["assumptions"])
        if answer["assumptions"].get("linked_statements"):
            from .three_statements import ThreeStatementForecast
            ThreeStatementForecast.validate(answer["assumptions"], company)
        self.require_reviewer(answer)
        changes = [] if state.get("ledger_enabled") else self.audit.changes(state["facts"], facts)
        return {"facts": facts, "company": company, "assumptions": answer["assumptions"],
                "issues": [], "audit": self.record(state, "inputs_confirmed",
                    reviewer=answer["reviewer"], note=answer["note"],
                    ocr_reviewed=bool(answer.get("ocr_reviewed")) if has_ocr else None,
                    historical_ledger_used=bool(state.get("ledger_enabled")),
                    ledger_basis=answer["assumptions"].get("ledger_basis"),
                    corrected_fields=[c["field"] for c in changes], fact_changes=changes,
                    original_facts=state["facts"], confirmed_facts=facts,
                    assumptions=answer["assumptions"])}

    def calculate(self, state):
        result = self.engine.calculate(state["company"], state["assumptions"])
        flags = self.engine.flags(state["company"], result)
        return {"calculations": result, "flags": flags,
                "audit": self.record(state, "calculated", tools="Python arithmetic", flags=flags)}

    def memo(self, state):
        error = ""
        try:
            memo = self.memo_writer.draft(state)
        except Exception as exc:
            memo = {}
            error = f"Memo unavailable ({safe_api_error(exc)}). Calculations are preserved; retry or reject."
        return {"memo": memo, "memo_error": error,
                "audit": self.record(state, "memo_drafted", success=not bool(error), error=error)}

    @staticmethod
    def require_reviewer(answer):
        if not answer.get("reviewer", "").strip() or not answer.get("note", "").strip():
            raise ValueError("Reviewer and review note are required.")

    def memo_review(self, state):
        answer = interrupt({"stage": "memo_review", "memo_error": state["memo_error"]})
        return self.prepare_memo_review(state,answer)

    def prepare_memo_review(self,state,answer):
        decision = answer.get("decision")
        if decision not in {"approved", "revise", "rejected"}:
            raise ValueError("Unknown review decision")
        if decision == "approved" and (state["memo_error"] or not state["memo"]):
            raise ValueError("An unavailable memo cannot be approved.")
        if decision == "approved" and state.get("assumptions", {}).get("linked_statements"):
            linked = state.get("calculations", {}).get("three_statements", {})
            if linked.get("status") != "balanced":
                raise ValueError("Linked statements contain an unresolved funding shortfall. Create an input revision with an explicit feasible financing plan before approval.")
        self.require_reviewer(answer)
        fingerprint = ApprovalPolicy.fingerprint(state) if decision == "approved" else ""
        return {"decision": decision, "feedback": answer["note"],
                "approved_fingerprint": fingerprint,
                "approval_policy_version": ApprovalPolicy.VERSION if decision == "approved" else "",
                "audit": self.record(state, "memo_" + decision, reviewer=answer["reviewer"],
                                      note=answer["note"], reviewed_memo=state["memo"],
                                      approved_fingerprint=fingerprint,
                                      approval_policy_version=ApprovalPolicy.VERSION)}

    def build(self, checkpointer=None):
        graph = StateGraph(DealState)
        names = ["retrieve", "extract", "validate", "input_review", "calculate", "memo", "memo_review"]
        for name in names:
            graph.add_node(name, getattr(self, name))
        for name, node in [("ledger_context",self.ledger.discover),("ledger_is",self.ledger.income),
                           ("ledger_bs",self.ledger.balance),("ledger_cf",self.ledger.cashflow),
                           ("ledger_review",self.ledger.review)]:
            graph.add_node(name,node)
        graph.add_edge("ledger_context","ledger_is")
        graph.add_edge("ledger_is","ledger_bs")
        graph.add_edge("ledger_bs","ledger_cf")
        graph.add_edge("ledger_cf","ledger_review")
        graph.add_edge("ledger_review","input_review")
        graph.add_conditional_edges("validate", lambda state:
            "input_review" if not state.get("ledger_enabled") else
            ("ledger_review" if state.get("reuse_ledger") else "ledger_context"),
            {"input_review":"input_review","ledger_review":"ledger_review","ledger_context":"ledger_context"})
        graph.add_edge(START, names[0])
        for first, second in zip(names, names[1:]):
            if first not in {"retrieve","validate"}:
                graph.add_edge(first, second)
        graph.add_conditional_edges("retrieve", lambda state: bool(state.get("reuse_facts")),
                                    {True: "validate", False: "extract"})
        graph.add_conditional_edges("memo_review", lambda state: state["decision"],
                                    {"revise": "memo", "approved": END, "rejected": END})
        return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
