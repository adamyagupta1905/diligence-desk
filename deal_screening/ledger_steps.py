"""Historical extraction/review graph nodes; no UI or valuation arithmetic."""
from copy import deepcopy
from langgraph.types import interrupt
from .audit import AuditLog
from .ledger import LedgerValidator, LedgerDiagnostics
from .llm import safe_api_error


def ledger_changes(original, edited):
    before={r["record_id"]:r for r in original}
    changes=[]
    for row in edited:
        old=before.get(row["record_id"])
        def canonical(r):
            return {k: (v.strip() or None) if isinstance(v,str) else v for k,v in (r or {}).items()}
        if canonical(old) != canonical(row):
            changes.append({"record_id":row["record_id"],"before":deepcopy(old),"after":deepcopy(row)})
    return changes


class HistoricalLedgerSteps:
    def __init__(self, extractor, audit=None):
        self.extractor=extractor
        self.audit=audit or AuditLog()

    def record(self,state,action,**details):
        return self.audit.append(state.get("audit",[]),action,**details)

    def discover(self,state):
        try:
            if self.extractor is None:
                raise ValueError("Historical extractor is not configured")
            context=self.extractor.discover(state["chunks"],state["model_name"])
            errors=[]
        except Exception as exc:
            context={"company_name":{"value":"","chunk_id":None,"quote":None},
                     "business":{"value":"","chunk_id":None,"quote":None},
                     "reporting_currency":{"value":"","chunk_id":None,"quote":None},"periods":[]}
            errors=["Historical discovery unavailable: "+safe_api_error(exc)]
        return {"ledger_context":context,"ledger_raw":[],"ledger_rows":[],"ledger_errors":errors,
                "ledger_retrieval":{},"ledger_reviewed":False,
                "audit":self.record(state,"historical_period_discovery",success=not bool(errors),errors=errors)}

    def statement(self,state,statement):
        errors=list(state.get("ledger_errors",[])); rows=[]; passages=[]
        if state["ledger_context"].get("periods"):
            try:
                rows,passages=self.extractor.statement(state["chunks"],state["ledger_context"],statement,state["model_name"])
            except Exception as exc:
                errors.append(f"{statement} extraction unavailable: "+safe_api_error(exc))
        else:
            errors.append(f"{statement} extraction skipped: annual periods were not established.")
        return {"ledger_raw":state.get("ledger_raw",[])+deepcopy(rows),
                "ledger_rows":state.get("ledger_rows",[])+deepcopy(rows),"ledger_errors":errors,
                "ledger_retrieval":{**state.get("ledger_retrieval",{}),statement:passages},
                "audit":self.record(state,"historical_statement_extracted",statement=statement,
                                    candidates=len(rows),passage_ids=passages,errors=errors)}

    def income(self,state):
        return self.statement(state,"IS")

    def balance(self,state):
        return self.statement(state,"BS")

    def cashflow(self,state):
        return self.statement(state,"CF")

    def review(self,state):
        answer=interrupt({"stage":"ledger_review","errors":state.get("ledger_errors",[])})
        return self.prepare_review(state,answer)

    def prepare_review(self,state,answer):
        """Pure preflight used before consuming a persisted interrupt answer."""
        context,rows=answer["context"],answer["rows"]
        errors,normalized=LedgerValidator.validate(context,rows,state["chunks"])
        previous_rows=state.get("ledger_rows",state["ledger_raw"])
        source_ids={r["record_id"] for r in previous_rows} | {r["record_id"] for r in state["ledger_raw"]}
        if not source_ids <= {r.get("record_id") for r in rows}:
            errors.append("Keep source candidates in the history; exclude with a reason instead of deleting them.")
        changes=ledger_changes(previous_rows,rows)
        for change in changes:
            if not str(change["after"].get("review_note") or "").strip():
                errors.append(change["record_id"]+": explain the correction, addition or exclusion in review_note.")
        if errors:
            raise ValueError("\n".join(errors))
        checks,derived=LedgerDiagnostics.analyze(normalized,context["periods"])
        if any(c["status"]=="fail" for c in checks) or state.get("ledger_errors"):
            if not answer.get("accept_exceptions"):
                raise ValueError("Inspect and acknowledge reconciliation/extraction exceptions with a review note.")
        if not answer.get("confirmed") or not answer.get("reviewer","").strip() or not answer.get("note","").strip():
            raise ValueError("Confirm the evidence review and enter your name and review rationale.")
        if any(p.get("extraction_method")=="ocr" for p in state["pages"]) and not answer.get("ocr_reviewed"):
            raise ValueError("Check the original scanned pages before confirming historical figures.")
        year=answer["selected_year"]
        if year not in [p["fiscal_year"] for p in context["periods"]]:
            raise ValueError("Select an evidenced historical fiscal year for valuation.")
        return {"ledger_rows":deepcopy(rows),"ledger_context_confirmed":deepcopy(context),
                "ledger_context_draft":deepcopy(context),
                "ledger_confirmed":normalized,"ledger_selected_year":year,"ledger_reviewed":True,
                "ledger_diagnostics":checks,"ledger_derived":derived,
                "audit":self.record(state,"historical_ledger_confirmed",reviewer=answer["reviewer"],
                    note=answer["note"],selected_year=year,row_changes=changes,
                    differences_from_extraction=ledger_changes(state["ledger_raw"],rows),
                    original_context=state["ledger_context"],confirmed_context=context,
                    confirmed_rows=normalized,exceptions_accepted=bool(answer.get("accept_exceptions")),
                    diagnostics=checks,derived=derived)}
