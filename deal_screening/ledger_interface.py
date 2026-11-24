"""Historical ledger review and read-only views; normalization stays in the domain layer."""
from copy import deepcopy
import csv
import io
import re
import uuid

from .ledger import METRICS, LedgerValidator, LedgerDiagnostics, LedgerBridge
from .ledger_steps import ledger_changes


ROW_TEMPLATE={"record_id":"","selected":True,"fiscal_year":None,"metric":"revenue",
    "reported_label":"","reported_value":"","currency":"","unit":"units",
    "sign_multiplier":1,"status":"found","chunk_id":"","quote":"",
    "context_chunk_id":"","context_quote":"","note":"","review_note":""}


def clean_rows(rows):
    result=[]
    for source in rows:
        row=deepcopy(source)
        if not row.get("record_id") and not row.get("reported_value") and not row.get("quote"):
            continue
        if not row.get("record_id"):
            row["record_id"]="MANUAL-"+uuid.uuid4().hex[:12]
        for key in ("reported_value","currency","chunk_id","quote","context_chunk_id","context_quote"):
            if row.get(key)=="":row[key]=None
        for key in ("fiscal_year","sign_multiplier"):
            value=row.get(key)
            if isinstance(value,float) and value.is_integer():row[key]=int(value)
        result.append(row)
    return result


def edited_context(metadata,periods):
    result={row["field"]:{"value":row.get("value") or "", "chunk_id":row.get("chunk_id") or None,
                           "quote":row.get("quote") or None} for row in metadata}
    result["periods"]=[]
    for row in periods:
        if row.get("fiscal_year") is None and not row.get("quote"):continue
        year=row.get("fiscal_year")
        if isinstance(year,float) and year.is_integer():year=int(year)
        result["periods"].append({"fiscal_year":year,"label":row.get("label") or "",
                                 "end_date":row.get("end_date") or None,
                                 "chunk_id":row.get("chunk_id") or "","quote":row.get("quote") or ""})
    return result


def csv_text(rows):
    stream=io.StringIO()
    fields=["record_id","statement","fiscal_year","metric","reported_label","reported_value","unit",
            "currency","sign_multiplier","normalized_value","origin","chunk_id","quote",
            "context_chunk_id","context_quote","review_note"]
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore");writer.writeheader()
    # CSV is an analyst-readable export, not executable spreadsheet instructions.
    for row in rows:
        sanitized={key:("'"+value if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")) and not re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?",value) else value)
                   for key,value in row.items()}
        writer.writerow(sanitized)
    return stream.getvalue()


def show_ledger(st,state):
    if not state.get("ledger_enabled"):return
    st.subheader("Historical statement ledger")
    if not state.get("ledger_reviewed"):
        st.caption("Unconfirmed extraction. Historical review is required before valuation.")
    else:
        rows=state.get("ledger_confirmed",[])
        st.caption(f"Confirmed {len(rows)} historical line items. Valuation year: FY{state['ledger_selected_year']}.")
        with st.expander("Confirmed historical rows, sources and normalized values",expanded=False):
            st.dataframe(rows,hide_index=True)
            st.download_button("Download confirmed historical ledger (CSV)",csv_text(rows),
                               file_name="historical_ledger.csv",mime="text/csv",key="ledger_csv")
        with st.expander("Historical coverage by statement and fiscal year"):
            st.dataframe([{"year":p["fiscal_year"],"statement":statement,
                "found_metrics":sum(r["fiscal_year"]==p["fiscal_year"] and r["statement"]==statement for r in rows),
                "available_metric_codes":", ".join(r["metric"] for r in rows if r["fiscal_year"]==p["fiscal_year"] and r["statement"]==statement)}
                for p in state["ledger_context_confirmed"]["periods"] for statement in ("IS","BS","CF")],hide_index=True)
            st.caption("An omitted line is unknown, not zero. Coverage counts are not extraction-confidence scores.")
        with st.expander("Historical reconciliations and separately derived metrics",expanded=True):
            st.dataframe(state.get("ledger_diagnostics",[]),hide_index=True)
            st.dataframe(state.get("ledger_derived",[]),hide_index=True)
            st.caption("Pass means an arithmetic check within displayed rounding tolerance, not source verification. CFO less capex is not automatically unlevered FCFF.")
    with st.expander("Original unreviewed extraction and retrieval coverage"):
        st.json({"context":state.get("ledger_context"),"candidates":state.get("ledger_raw",[]),
                 "errors":state.get("ledger_errors",[]),"statement_passages":state.get("ledger_retrieval",{})})


def review_form(st,service,view,user_error):
    state=view.state;prefix=view.revision["id"]+"_"+view.checkpoint_id
    st.subheader("Historical review: confirm annual statements")
    for error in state.get("ledger_errors",[]):st.error(error)
    st.write("Check entity, annual periods, table headings, units, signs and quotes against the original document. "
             "Keep source candidates; uncheck selected and explain exclusions instead of deleting. "
             "Every correction/addition needs a review_note. Preview the checks before confirming.")
    raw_context=state.get("ledger_context_draft",state["ledger_context"])
    if len(raw_context.get("periods",[])) < 3:
        st.info("Fewer than three annual periods were disclosed. This is partial history; no years will be fabricated.")
    with st.form("historical_"+prefix):
        metadata=st.data_editor([{ "field":field, **{k:"" if v is None else v for k,v in raw_context[field].items()}}
                    for field in ("company_name","business","reporting_currency")],
                    disabled=["field"],hide_index=True,key="ledger_context_"+prefix)
        period_rows=raw_context["periods"] or [{"fiscal_year":None,"label":"","end_date":"","chunk_id":"","quote":""}]
        periods=st.data_editor([{k:"" if v is None and k!="fiscal_year" else v for k,v in row.items()} for row in period_rows],
                              num_rows="dynamic",hide_index=True,key="ledger_periods_"+prefix)
        st.write("Reported candidates — sign_multiplier converts presentation only; normalization is calculated by Python")
        rows=state.get("ledger_rows") or [ROW_TEMPLATE]
        draft=st.data_editor([{k:"" if v is None and k not in ("fiscal_year","sign_multiplier") else v for k,v in row.items()} for row in rows],
            num_rows="dynamic",disabled=["record_id"],hide_index=True,width="stretch",key="ledger_rows_"+prefix,
            column_config={"metric":st.column_config.SelectboxColumn(options=list(METRICS)),
                           "unit":st.column_config.SelectboxColumn(options=["units","thousands","millions","percent","decimal"]),
                           "status":st.column_config.SelectboxColumn(options=["found","missing","conflicting"]),
                           "fiscal_year":st.column_config.NumberColumn(step=1),
                           "sign_multiplier":st.column_config.NumberColumn(step=1),
                           "selected":st.column_config.CheckboxColumn(),
                           "reported_value":st.column_config.TextColumn(),"review_note":st.column_config.TextColumn()})
        years=sorted({int(p["fiscal_year"]) for p in periods if isinstance(p.get("fiscal_year"),(int,float))},reverse=True)
        year=st.selectbox("Historical year used for valuation",years or [None],
            index=years.index(state["ledger_selected_year"]) if state.get("ledger_selected_year") in years else 0,
            key="ledger_year_"+prefix)
        reviewer=st.text_input("Historical reviewer name",key="ledger_reviewer_"+prefix)
        note=st.text_area("Historical review rationale and exception explanations",key="ledger_note_"+prefix)
        checked=st.checkbox("I checked the entity, annual periods, values, units, signs and source evidence.",key="ledger_check_"+prefix)
        accept=st.checkbox("I reviewed and accept the documented reconciliation/extraction exceptions.",key="ledger_exceptions_"+prefix)
        ocr=st.checkbox("I checked historical OCR figures against the original scans.",key="ledger_ocr_"+prefix) if any(p.get("extraction_method")=="ocr" for p in state["pages"]) else True
        preview=st.form_submit_button("Preview historical normalization and checks")
        confirm=st.form_submit_button("Confirm historical ledger")
    if preview or confirm:
        try:
            context=edited_context(metadata,periods);rows=clean_rows(draft)
            errors,normalized=LedgerValidator.validate(context,rows,state["chunks"])
            if errors:raise ValueError("\n".join(errors))
            checks,derived=LedgerDiagnostics.analyze(normalized,context["periods"])
            st.session_state["ledger_preview_"+prefix]={"rows":normalized,"checks":checks,"derived":derived}
            if confirm:
                service.resume(view.revision["id"],{"context":context,"rows":rows,"selected_year":year,
                    "reviewer":reviewer,"note":note,"confirmed":checked,"accept_exceptions":accept,
                    "ocr_reviewed":ocr},view.checkpoint_id)
                st.rerun()
        except Exception as exc:user_error(st,exc)
    if "ledger_preview_"+prefix in st.session_state:
        result=st.session_state["ledger_preview_"+prefix]
        st.write("Preview from your last submitted form (not yet a confirmed ledger)")
        st.dataframe(result["rows"],hide_index=True)
        st.dataframe(result["checks"],hide_index=True)
        st.dataframe(result["derived"],hide_index=True)


def bridge_controls(st,state):
    year=state["ledger_selected_year"]
    selected={r["metric"]:r for r in state["ledger_confirmed"] if r["fiscal_year"]==year}
    basis=state.get("assumptions",{}).get("ledger_basis",{})
    default=basis.get("net_debt","reported" if "net_debt" in selected else "debt_less_cash")
    net=st.selectbox("Net debt input basis",["reported","debt_less_cash"],index=0 if default=="reported" else 1,
        format_func=lambda v:"Reported net debt" if v=="reported" else "Derived: short-term debt + long-term debt - cash",
        key="net_basis_"+state["revision_id"])
    derive=st.checkbox("Use derived EBITDA = operating income + disclosed D&A (verify matching scope)",
                        value=bool(basis.get("use_derived_ebitda",False)),key="ebitda_basis_"+state["revision_id"])
    facts,company=LedgerBridge.build(state["ledger_context_confirmed"],state["ledger_confirmed"],year,
                                   net_debt_basis=net,use_derived_ebitda=derive)
    st.dataframe([{k:f.get(k) for k in ("field","value","origin","note","dependencies")} for f in facts],hide_index=True)
    st.caption("Valuation inputs are built from the confirmed ledger. Revise the historical ledger in a new revision to correct these records. Derived values retain component record IDs.")
    return facts,company,{"net_debt":net,"use_derived_ebitda":derive}
