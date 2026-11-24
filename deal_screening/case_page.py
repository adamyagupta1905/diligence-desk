"""The page for one saved case: header, progress tracker and tabs.

app.py decides WHICH case and revision to show; this module decides HOW to show
it. It only reads state and calls the review forms. No maths, no model calls.
"""
from pathlib import Path

from .forms import input_review_form, memo_review_form, revision_form, user_error
from .interface import show_comparison, show_results
from .ledger_interface import show_ledger, review_form as ledger_review_form
from .reporting import report_text


# ---------------------------------------------------------------- progress
def progress_steps(view):
    """Return [(label, status)] where status is 'done', 'current' or 'todo'."""
    state, waiting = view.state, set(view.next) if view.interrupted else set()
    has_calc = bool(state.get("calculations"))
    # New revisions start with decision="pending", so only real outcomes count.
    decision = state.get("decision") if state.get("decision") in {"approved", "rejected"} else None
    steps = [("Read & search", "done" if state.get("chunks") else "current")]
    if state.get("ledger_enabled"):
        steps.append(("Ledger review", "current" if "ledger_review" in waiting
                      else "done" if state.get("ledger_reviewed") else "todo"))
    else:
        steps.append(("AI extraction", "done" if state.get("facts") else "todo"))
    steps.append(("Input review", "current" if "input_review" in waiting
                  else "done" if has_calc else "todo"))
    steps.append(("Valuation", "done" if has_calc else "todo"))
    steps.append(("Memo review", "current" if "memo_review" in waiting
                  else "done" if decision else "todo"))
    final = {"approved": "Approved", "rejected": "Rejected"}.get(decision, "Decision")
    steps.append((final, "done" if decision in {"approved", "rejected"} else "todo"))
    return steps


def show_progress(st, view):
    badge = {"done": ":green-badge[✓ {}]", "current": ":blue-badge[● {}]", "todo": ":gray-badge[{}]"}
    st.markdown("  →  ".join(badge[status].format(label) for label, status in progress_steps(view)))


# ---------------------------------------------------------------- header
def show_header(st, view, title):
    state = view.state
    st.header(title)
    st.caption(f"Revision {view.revision['number']} · {view.revision['document_name']} · "
               f"ID {view.revision['id'][:8]}… · SHA256 {view.revision['document_hash'][:12]}…")
    if view.status == "approved":
        st.success("Current revision approved by analyst.", icon="✅")
    elif view.status == "superseded":
        st.warning(f"Historical revision — recorded decision: {state.get('decision', 'pending')}. "
                   "Its approval does not apply to the current revision.", icon="🕘")
    elif view.status == "stale_approval":
        st.error("Approval no longer matches the reviewed content. Create a revision and review again.")
    elif view.interrupted:
        st.info("Waiting for your review — open the **Review** tab.", icon="📝")
    else:
        st.info("Current revision status: " + view.status)
    if state:
        show_progress(st, view)


def show_retry(st, service, view):
    state = view.state
    if not state:
        st.warning("Source saved, but processing has not completed. Retry to recover this revision.")
    elif view.next and not view.interrupted:
        st.warning("Processing was interrupted before a review checkpoint. You can retry below.")
    if view.is_current and (not state or (view.next and not view.interrupted)):
        if st.button("Retry interrupted processing"):
            try:
                with st.spinner("Resuming processing…"):
                    service.retry_processing(view.revision["id"], view.checkpoint_id)
                st.rerun()
            except Exception as exc:
                user_error(st, exc)


# ---------------------------------------------------------------- tabs
def review_tab(st, service, view):
    state, waiting = view.state, set(view.next) if (view.is_current and view.interrupted) else set()
    show_ledger(st, state)
    if "ledger_review" in waiting:
        ledger_review_form(st, service, view, user_error)
    if "input_review" in waiting:
        input_review_form(st, service, view)
    show_results(st, state)
    if "memo_review" in waiting:
        memo_review_form(st, service, view)
    if not waiting and not state.get("calculations") and not state.get("ledger_enabled"):
        st.caption("Nothing to review on this revision.")


def evidence_tab(st, view):
    state = view.state
    chunks, retrieved = state.get("chunks", []), {c["id"] for c in state.get("retrieved", [])}
    run = state.get("retrieval") or {"method": "BM25 keyword retrieval (recorded before hybrid search)"}
    added = set(run.get("added_by_embeddings", []))
    cited = {}
    for fact in state.get("facts", []):
        if fact.get("chunk_id") and fact.get("status") == "found":
            cited.setdefault(fact["chunk_id"], []).append(fact["field"])

    st.subheader("How passages were found")
    a, b, c = st.columns(3)
    a.markdown(f"**Search method**  \n{run['method']}")
    b.markdown(f"**Sent to the AI**  \n{len(retrieved)} of {len(chunks)} passages")
    c.markdown(f"**Added by embeddings**  \n{len(added)} passage(s) keyword search alone would miss")
    if run.get("error"):
        st.warning("Embedding search failed, so keyword search was used alone. " + run["error"])
    st.caption("Hybrid search ranks passages twice — by matching words (BM25) and by meaning "
               "(embeddings) — then merges the rankings. See docs/ARCHITECTURE.md.")

    only_sent = st.toggle("Show only passages sent to the AI", value=True, key="evidence_only_" + view.revision["id"])
    for chunk in chunks:
        if only_sent and retrieved and chunk["id"] not in retrieved:
            continue
        badges = []
        if chunk["id"] in added:
            badges.append(":violet-badge[added by embeddings]")
        elif chunk["id"] in retrieved:
            badges.append(":blue-badge[sent to AI]")
        else:
            badges.append(":gray-badge[not selected]")
        if chunk.get("extraction_method") == "ocr":
            badges.append(":orange-badge[OCR]")
        for field in cited.get(chunk["id"], []):
            badges.append(f":green-badge[cited for {field}]")
        with st.container(border=True):
            st.markdown(f"**{chunk['id']}** · page {chunk['page']}  " + " ".join(badges))
            st.text(chunk["text"])

    ocr_pages = [p for p in state.get("pages", []) if p.get("extraction_method") == "ocr"]
    if ocr_pages:
        st.subheader("OCR provenance")
        st.warning("OCR was used. Recognition confidence does not guarantee correct financial figures; inspect the original scan.")
        st.dataframe([{"page": p["page"], "method": "Local English OCR", "mean_word_score_0_to_100": p["ocr_confidence"]}
                      for p in ocr_pages], hide_index=True)
        page_number = st.selectbox("Original scanned page", [p["page"] for p in ocr_pages])
        if st.button("Show original scanned page"):
            from .ocr import LocalOCREngine
            image = LocalOCREngine.render_page(view.revision["source"], page_number - 1, dpi=140)
            st.image(image, caption=f"Original page {page_number}")
            image.close()
        with st.expander("Recognized lines and pixel bounding boxes"):
            st.json(ocr_pages)


def compare_tab(st, service, view):
    before = service.view(view.revision["parent_id"])
    show_comparison(st, before.state, view.state,
                    before.revision["initial_state"]["pages"], view.revision["initial_state"]["pages"])


def history_tab(st, service, view, case_id, revisions):
    state = view.state
    st.subheader("Downloads")
    left, right = st.columns(2)
    left.download_button("Download source document", view.revision["source"],
                         file_name=Path(view.revision["document_name"]).name, key="source_download")
    if state.get("calculations"):
        # Assess status when generating this snapshot; never just trust decision='approved'.
        current_id = service.repository.case(case_id)["active_revision_id"]
        label = "Download approved report" if view.status == "approved" else "Download draft / historical report"
        right.download_button(label, report_text(state, current_revision_id=current_id),
                              file_name=f"screening_v{view.revision['number']}.txt", mime="text/plain",
                              key="report_download")
    st.subheader("Versions")
    st.dataframe([{k: r[k] for k in ("number", "kind", "document_name", "author", "reason", "created_at")}
                  for r in revisions], hide_index=True)
    with st.expander("Case events"):
        st.json(service.repository.events(case_id))
    with st.expander("Audit trail and reviewed data"):
        st.json(state.get("audit", []))


# ---------------------------------------------------------------- page
def show_case(st, service, view, case_id, title, revisions):
    show_header(st, view, title)
    show_retry(st, service, view)
    state = view.state
    names = ["📝 Review", "🔎 Evidence"]
    has_parent = bool(view.revision["parent_id"] and state)
    if has_parent:
        names.append("↔️ Compare")
    names.append("🗂 History & downloads")
    can_revise = view.is_current and bool(state)
    if can_revise:
        names.append("✏️ New revision")
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["📝 Review"]:
        review_tab(st, service, view)
    with tabs["🔎 Evidence"]:
        evidence_tab(st, view)
    if has_parent:
        with tabs["↔️ Compare"]:
            compare_tab(st, service, view)
    with tabs["🗂 History & downloads"]:
        history_tab(st, service, view, case_id, revisions)
    if can_revise:
        with tabs["✏️ New revision"]:
            revision_form(st, service, view)
