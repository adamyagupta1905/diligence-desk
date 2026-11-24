# DiligenceDesk

AI-assisted investment screening: cited LLM extraction, analyst review, Python-only
valuation (EV/EBITDA, FCFF DCF), sourced memo, content-bound approval.
Stack: Python 3.13, LangGraph (interrupts + SQLite checkpoints), Gemini, BM25 + embedding
hybrid retrieval, Pydantic, Streamlit, Tesseract OCR.

## Commands
- Setup: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests (offline, no key, ~10 s, 136 tests): `.venv/bin/python app.py --check`
- Run app: `.venv/bin/python -m streamlit run app.py`
- Key-free demo: `DEMO_MODE=1 GEMINI_API_KEY= .venv/bin/python -m streamlit run app.py`
  (workspace launch config `diligence-public-demo`, port 8600)
- Live evals (paid; only with the user's OK):
  `.venv/bin/python evaluation/run_eval.py --live`,
  `.venv/bin/python evaluation/benchmark_retrieval.py --live`
- Re-record demo cassette (paid; only with OK): `demo/record_demo.py`

## Layout (`deal_screening/`)
- `llm.py`: the only module that imports the Google SDK. Everything else gets a client injected.
- `workflow.py`: LangGraph steps and the two human interrupts.
- `cases.py`: CaseService: persistence, start/resume graph, stale-tab checks. `storage.py`: SQLite.
- `retrieval.py`, `documents.py`, `ocr.py`: reading, chunking, BM25+embedding RRF search.
- `ai.py`: extraction and memo prompts. `validation.py`: verifies quotes/citations.
- `finance.py`, `dcf.py`, `operating_forecast.py`, `fixed_assets.py`, `three_statements.py`: all maths.
- `ledger*.py`: multi-year historical ledger. `approvals.py`, `audit.py`: sign-off and audit trail.
- `app.py`, `case_page.py`, `forms.py`, `*_forms.py`, `*interface.py`: Streamlit UI only.
- `replay.py`, `demo.py`, `demo/cassette.json`: recorded-response demo mode.
- Docs: `docs/ARCHITECTURE.md`, `METHODOLOGY.md`, `EVALUATION.md`, `DEPLOYMENT.md`, `DECISIONS.md` (ADRs).
- Claude tooling: `.claude/settings.json` (permissions), `.claude/agents/` (read-only subagents:
  `docs-drift-checker`, `dependency-checker`), `.claude/skills/` (`release-checklist`, `record-demo`).
  CI: `.github/workflows/ci.yml` (offline tests), `claude-review.yml` (PR review, label `claude-review`).

## Invariants (never break)
- Gemini extracts, labels and drafts; **Python does every calculation.** Memo claims cite `CALC:` outputs.
- Every extracted fact carries a verbatim quote that Python verifies exists in the cited passage.
- Missing is never zero. Conflicts are flagged, not resolved. Adjusted EBITDA is never taken as reported.
  No balancing plugs. Forecast assumptions are never labelled as reported facts.
- Validate analyst input **before** `Command(resume=...)`; LangGraph caches interrupt answers.
- Revisions are immutable; any content change invalidates approvals (SHA-256 fingerprint).
  Audit history and stale-tab rejection must survive every change.
- Domain modules import no UI, DB or SDK code.
- Keep old screening modes working. Never overwrite `data/*.sqlite3`.

## Conventions
- Tests are `unittest` under `tests/`, offline with fakes and temp DBs; fixtures in
  `tests/expected_results.json`, `tests/helpers.py`.
- Bugs found by evaluation get a regression test.
- Source and tests are the authority over narrative docs; update docs when behaviour changes.
- Never edit: `data/`, `evaluation/results/` (generated), `demo/cassette.json` (recorded).

## Effort and verification
- Plan at high effort for changes to valuation maths, workflow interrupts, approvals or storage;
  execute small, well-specified edits directly. Subagents run at low/medium and never edit.
- Full suite + `invariant-reviewer` when a change touches maths modules, `validation.py`, `ai.py`,
  `workflow.py`, `cases.py`, `approvals.py`, `audit.py` or `storage.py`. A single UI-only file
  edit needs only the relevant test module. Always run the full suite before calling a task done.
