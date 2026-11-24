"""Review forms: each one collects a human decision and hands it to CaseService.

Every form is bound to the checkpoint it was shown with, so a stale browser tab
cannot submit an answer for a checkpoint that has already moved on.
"""
import os

from .examples import SAMPLE_TEXT
from .validation import InputValidator
from .valuation_forms import choose_method, assumptions_form
from .ledger_interface import bridge_controls

def user_error(st, exc):
    st.error(str(exc) if isinstance(exc, (ValueError, UnicodeError))
             else f"Operation could not finish ({type(exc).__name__}). Your saved case is retained.")


def new_case_form(st, service):
    mode = st.radio("Document", ["Built-in fictional example", "Upload PDF or text"], horizontal=True)
    upload = st.file_uploader("Company document", type=["pdf", "txt"]) if mode.startswith("Upload") else None
    force_ocr = st.checkbox("Force OCR on every page (use for partial/broken text layers)", key="new_force_ocr") if upload and upload.name.lower().endswith(".pdf") else False
    if mode.startswith("Built"):
        with st.expander("Read the fictional source document"):
            st.text(SAMPLE_TEXT.replace("\f", "\n--- PAGE BREAK ---\n"))
    ledger_enabled = st.checkbox("Extract a multi-year historical statement ledger", key="new_ledger_enabled")
    if ledger_enabled:
        st.caption("Historical mode makes four extraction requests: annual-period discovery, income statement, balance sheet and cash-flow statement. Each stage is saved. Missing lines remain unknown.")
    st.caption("Starting a screening sends retrieved passages to Gemini. Documents and review history are saved locally.")
    with st.form("new_case"):
        title = st.text_input("Case title", value="Northstar screening", key="new_title")
        author = st.text_input("Your name", key="new_author")
        reason = st.text_area("Case note", value="Initial review of fictional company information.", key="new_reason")
        submitted = st.form_submit_button("Start new screening", type="primary")
    if submitted:
        try:
            if not os.getenv("GEMINI_API_KEY", "").strip():
                raise ValueError("Set GEMINI_API_KEY in the .env file first.")
            if mode.startswith("Upload") and upload is None:
                raise ValueError("Select a document first.")
            source = upload.getvalue() if upload else SAMPLE_TEXT.encode()
            name = upload.name if upload else "fictional_northstar.txt"
            with st.spinner("Saving the case and extracting cited facts…"):
                revision_id = service.create(title, source, name,
                    os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), author, reason, force_ocr=force_ocr, ledger_enabled=ledger_enabled)
            revision = service.repository.revision(revision_id)
            st.session_state._open_case = revision["case_id"]
            st.session_state._open_revision = revision_id
            st.rerun()
        except Exception as exc:
            user_error(st, exc)


def input_review_form(st, service, view):
    state = view.state
    st.subheader("Checkpoint 1: confirm evidence and assumptions")
    if state.get("extraction_error"):
        st.error(state["extraction_error"])
    method, years = choose_method(st, state, view.revision["id"])
    ledger_basis = None
    ledger_facts = None
    if state.get("ledger_enabled"):
        try:
            ledger_facts, company_for_template, ledger_basis = bridge_controls(st, state)
            display_issues = []
            ebitda = company_for_template["ebitda"]
            if method == "ev_ebitda" and (ebitda is None or ebitda <= 0):
                display_issues = ["Positive EBITDA is unavailable on this basis. "
                                  "Select a supported derived EBITDA or choose DCF."]
        except ValueError as exc:
            st.error(str(exc))
            st.info("Create a new input revision to revisit the historical ledger if source data needs correction.")
            return
    else:
        display_issues, company_for_template = InputValidator.facts(state["facts"], state["chunks"], method=method)
    for issue in display_issues:
        st.warning(issue)
    st.write("Check each quote, unit and period. Numbers use full units (50000000); "
             "percentages use decimals (0.18). Monetary facts require matching currency "
             "and period. Missing optional growth/concentration may remain unknown.")
    prefix = view.revision["id"]
    # Bind widget submissions to the checkpoint shown to the analyst. If
    # another tab advances it, the old form submission cannot target this form.
    with st.form("inputs_" + prefix + "_" + view.checkpoint_id + "_" + method + "_" + str(years)):
        if ledger_facts is not None:
            edited = ledger_facts
        else:
            rows = [{k: "" if v is None else str(v) for k, v in f.items()} for f in state["facts"]]
            edited = st.data_editor(rows, disabled=["field"], num_rows="fixed", hide_index=True, width="stretch")
        assumptions = assumptions_form(st, method, years, state, company_for_template)
        if ledger_basis is not None:
            assumptions["ledger_basis"] = ledger_basis
        has_ocr = any(p.get("extraction_method") == "ocr" for p in state["pages"])
        ocr_checked = st.checkbox("I compared OCR figures, minus signs and units with the original scan.", key="ocr_check_" + prefix) if has_ocr else True
        reviewer = st.text_input("Analyst name", key="input_reviewer_" + prefix)
        note = st.text_area("Input-review note", key="input_note_" + prefix)
        checked = st.checkbox("I checked supporting passages, units, periods, and conflicts.", key="input_check_" + prefix)
        submitted = st.form_submit_button("Confirm inputs and calculate")
    if submitted:
        try:
            facts = [dict(row, **{k: None if row.get(k) == "" else row.get(k)
                                 for k in ("value", "chunk_id", "quote", "currency", "period")}) for row in edited]
            if ledger_facts is not None:
                issues = display_issues
            else:
                issues = InputValidator.facts(facts, state["chunks"], method=method)[0]
            InputValidator.assumptions(assumptions)
            if issues:
                raise ValueError("\n".join(issues))
            if not ocr_checked:
                raise ValueError("Check the OCR confirmation after inspecting the original scanned pages.")
            if not checked or not reviewer.strip() or not note.strip():
                raise ValueError("Check the confirmation box and enter your name and review note.")
            with st.spinner("Calculating and drafting the memo…"):
                service.resume(prefix, {"facts": facts, "assumptions": assumptions,
                    "reviewer": reviewer.strip(), "note": note.strip(), "ocr_reviewed": ocr_checked}, view.checkpoint_id)
            st.rerun()
        except Exception as exc:
            user_error(st, exc)


def memo_review_form(st, service, view):
    st.subheader("Checkpoint 2: review the memo")
    prefix = view.revision["id"]
    with st.form("memo_" + prefix + "_" + view.checkpoint_id):
        linked_blocked = view.state.get("assumptions", {}).get("linked_statements") and view.state.get("calculations", {}).get("three_statements", {}).get("status") != "balanced"
        if linked_blocked:
            st.error("Approval requires resolving the linked-statement funding shortfall in a new input revision.")
        choices = ["revise", "rejected"] if view.state.get("memo_error") or linked_blocked else ["approved", "revise", "rejected"]
        decision = st.selectbox("Decision", choices, format_func=lambda d: {
            "approved": "Approve screening memo", "revise": "Request revision / retry",
            "rejected": "Reject screening memo"}[d])
        reviewer = st.text_input("Memo reviewer name", key="memo_reviewer_" + prefix)
        note = st.text_area("Review note / revision instructions", key="memo_note_" + prefix)
        # A revised draft needs a new acknowledgement; do not reuse a checked
        # box from an earlier memo on this same document revision.
        checked = st.checkbox("I checked the memo against evidence, assumptions, and Python outputs.",
                              key="memo_check_" + prefix + "_" + view.checkpoint_id)
        submitted = st.form_submit_button("Submit review")
    if submitted:
        try:
            if not reviewer.strip() or not note.strip() or (decision == "approved" and not checked):
                raise ValueError("Enter your name and note. Approval also requires the confirmation box.")
            with st.spinner("Recording review…"):
                service.resume(prefix, {"decision": decision, "reviewer": reviewer.strip(),
                    "note": note.strip()}, view.checkpoint_id)
            st.rerun()
        except Exception as exc:
            user_error(st, exc)


def revision_form(st, service, view):
    with st.container():
        st.info("A new revision immediately requires fresh input and memo review. Earlier approvals "
                "remain historical and cannot approve the new revision.")
        kind = st.radio("Revision type", ["Revise inputs or assumptions", "Replace document", "Re-extract historical ledger"], key="revision_kind")
        ledger_enabled = st.checkbox("Include historical statement ledger",
                                     value=bool(view.state.get("ledger_enabled")),
                                     key="revision_ledger_enabled_" + view.revision["id"])
        upload = st.file_uploader("Revised company document", type=["pdf", "txt"], key="revised_document") if kind == "Replace document" else None
        force_ocr = st.checkbox("Force OCR for this replacement PDF", key="replacement_force_ocr") if upload and upload.name.lower().endswith(".pdf") else False
        with st.form("revision_" + view.revision["id"] + "_" + view.checkpoint_id):
            author = st.text_input("Revision author", key="revision_author")
            reason = st.text_area("What changed and why?", key="revision_reason")
            checked = st.checkbox("I understand that this creates an unapproved current revision.", key="revision_check")
            submitted = st.form_submit_button("Create revision")
        if submitted:
            try:
                if not checked:
                    raise ValueError("Confirm that a fresh review will be required.")
                if kind == "Replace document" and upload is None:
                    raise ValueError("Select the revised document.")
                with st.spinner("Saving and preparing the new revision…"):
                    if kind == "Replace document":
                        revision_id = service.replace_document(view.revision["id"], upload.getvalue(), upload.name,
                                                               author, reason, view.checkpoint_id, force_ocr=force_ocr, ledger_enabled=ledger_enabled)
                    else:
                        reextract = kind == "Re-extract historical ledger"
                        revision_id = service.revise(view.revision["id"], author, reason, view.checkpoint_id,
                                                     ledger_enabled=True if reextract else ledger_enabled,
                                                     reextract_ledger=reextract)
                st.session_state._open_revision = revision_id
                st.rerun()
            except Exception as exc:
                user_error(st, exc)

