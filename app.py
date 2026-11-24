"""DiligenceDesk — Streamlit entry point.

This file only wires things together and picks which page to show:
  - make_service(): builds the AI clients, search and saved-case service
  - main(): sidebar (choose case and revision) → new-case form or case page
The page layout lives in deal_screening/case_page.py and the review forms in
deal_screening/forms.py.

From the repository root:
    .venv/bin/python -m streamlit run app.py
    .venv/bin/python app.py --check
"""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from deal_screening.ai import FactExtractor, MemoWriter
from deal_screening.case_page import show_case
from deal_screening.cases import CaseService
from deal_screening.forms import new_case_form
from deal_screening.ledger_extraction import LedgerExtractor
from deal_screening.llm import GeminiClient, GeminiEmbedder
from deal_screening.reporting import LIMITATIONS
from deal_screening.retrieval import make_retriever
from deal_screening.storage import CaseRepository
from deal_screening.workflow import ScreeningWorkflow

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def retrieval_mode():
    """RETRIEVAL_MODE=bm25 turns off embedding calls; default is hybrid search."""
    return os.getenv("RETRIEVAL_MODE", "hybrid").strip().lower()


def make_service():
    directory = Path(os.getenv("DEAL_SCREENING_DATA_DIR", str(ROOT / "data")))
    key = os.getenv("GEMINI_API_KEY", "")
    client = GeminiClient(key)
    mode = retrieval_mode()
    embedder = None
    if key.strip():
        embedder = GeminiEmbedder(key, os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"))

    def workflow():
        return ScreeningWorkflow(
            FactExtractor(client), MemoWriter(client),
            retriever=make_retriever(embedder, mode, top_k=4),
            ledger_extractor=LedgerExtractor(client, retriever=make_retriever(embedder, mode, top_k=3)))
    return CaseService(CaseRepository(directory), workflow)


def sidebar(st, service):
    """Case and revision pickers. Returns (case_id or 'new', revisions, revision_id)."""
    st.sidebar.title("📊 DiligenceDesk")
    cases = service.repository.list_cases()
    names = {c["id"]: c["title"] for c in cases}
    pending = st.session_state.pop("_open_case", None)
    if pending:
        st.session_state.case_selector = pending
    selected = st.sidebar.selectbox("Saved case", ["new"] + list(names),
        format_func=lambda key: "＋ New case" if key == "new" else names[key], key="case_selector")
    revisions, revision_id = [], None
    if selected != "new":
        revisions = service.repository.revisions(selected)
        active = service.repository.case(selected)["active_revision_id"]
        labels = {r["id"]: f"v{r['number']} — {'Current' if r['id'] == active else 'Historical'} — {r['kind']}"
                  for r in revisions}
        selection_key = "revision_selector_" + selected
        pending = st.session_state.pop("_open_revision", None)
        if pending in labels:
            st.session_state[selection_key] = pending
        revision_id = st.sidebar.selectbox("Revision", list(labels), format_func=lambda key: labels[key],
                                           key=selection_key)
    st.sidebar.divider()
    has_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    mode = retrieval_mode()
    search = "Hybrid (keywords + embeddings)" if has_key and mode != "bm25" else "Keywords only (BM25)"
    st.sidebar.markdown(f"**Gemini key:** {'✅ set' if has_key else '⚠️ missing'}  \n**Search:** {search}")
    st.sidebar.caption("Cases, source documents and graph checkpoints persist locally across restarts.")
    return selected, names, revisions, revision_id


def intro(st):
    st.title("DiligenceDesk")
    st.markdown("Upload a company document → AI finds and cites the key facts → **you check them** → "
                "Python values the company → AI drafts a memo → **you approve it**.")
    st.caption("Fictional/public documents only · Saved local cases · No investment authorization")
    with st.expander("How it works and limitations"):
        st.markdown(
            "1. **Search (RAG):** the document is cut into passages; hybrid search picks the ones "
            "relevant to each fact.\n"
            "2. **Extract:** Gemini returns each fact with a verbatim quote and passage ID.\n"
            "3. **Checkpoint 1:** you confirm or correct facts and set valuation assumptions.\n"
            "4. **Calculate:** Python does all the maths (EV/EBITDA or DCF).\n"
            "5. **Memo + Checkpoint 2:** Gemini drafts a referenced memo; you approve, revise or reject.")
        st.write(LIMITATIONS)
        st.write("A new document or input revision needs fresh approval. Reviewer names are self-entered. "
                 "Comparison shows text and figure changes; it does not propose legal edits.")


def main():
    import streamlit as st
    st.set_page_config(page_title="DiligenceDesk", page_icon="📊", layout="wide")
    service = make_service()
    selected, names, revisions, revision_id = sidebar(st, service)
    if selected == "new":
        intro(st)
        new_case_form(st, service)
        return
    show_case(st, service, service.view(revision_id), selected, names[selected], revisions)


if __name__ == "__main__":
    if "--check" in sys.argv:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    else:
        main()
