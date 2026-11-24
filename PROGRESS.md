# Progress

_Last updated: 2026-11-23_

## Current goal
DiligenceDesk v1 is complete and public. Remaining work is polish; the next portfolio
project is an infrastructure project-finance screener reusing this pipeline.

## Done
- [x] Cited extraction, hybrid RAG, two LangGraph human checkpoints, Python valuation, sourced memo
- [x] Historical ledger, OCR, durable cases, revisions, content-bound approvals
- [x] Evaluation on 20 synthetic infrastructure reviews + retrieval benchmark (docs/EVALUATION.md)
- [x] Key-free public demo mode with recorded Gemini responses (gemini-3.8-flash, 2026-11-23)
- [x] Pushed to github.com/adamyagupta1905/diligence-desk

- [x] Production Claude Code stack (PR #1, 2026-11-23): `.github/workflows/ci.yml`
  (offline tests + apt Tesseract, since `test_advanced.py` runs real OCR), opt-in
  `claude-review.yml`, `.claude/settings.json` permissions, read-only project agents,
  `release-checklist` + `record-demo` skills, `docs/DECISIONS.md` (ADR-001…006)

## Next up
1. Merge the `claude-production-stack` PR into main (CI green).
2. Optional, paid per run: to use Claude PR review, install the Claude GitHub App, add
   repo secret `ANTHROPIC_API_KEY`, create label `claude-review`.
3. Run `/portfolio-release` and fix anything it flags.

## Blockers / open questions
- None.

## Decisions (newest first)
- 2026-11-23 Subagents read-only; Claude PR review only on label `claude-review`.
  **Why:** one writer keeps diffs reviewable; each review is a paid call (ADR-006).
- 2026-11-23 Replaced zip migration bundles with CLAUDE.md + PROGRESS.md + git.
  **Why:** a fresh session needs repo context, not a copied snapshot that goes stale.
- No vector database. **Why:** documents capped at 30 pages (~100 passages); in-memory
  cosine search takes milliseconds.

## Verification status
- Tests: 136 passed, `.venv/bin/python app.py --check`, 2026-11-23
- Evals: last live run 2026-11-22 (see docs/EVALUATION.md)
- CI: `tests` workflow passed on GitHub, branch `claude-production-stack`, 2026-11-24 (1m 3s)
- Not verified: Claude review workflow (needs secret + app; never run); new agents/skills not yet invoked
  (they load in a new session started in this folder)
