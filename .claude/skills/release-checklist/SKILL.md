---
name: release-checklist
description: Steps to ship a change to DiligenceDesk safely (tests, invariant review, docs drift, secrets, PROGRESS.md, commit, push, CI). Use when the user says "release", "ship this", "ready to push" or "cut a version" in this repo.
---

# DiligenceDesk release checklist

Report each step as pass / fix / skipped (with why). Stop at the first failure and fix it first.

1. **Scope.** `git status` and `git diff --stat`. Name what is shipping in one line.
   No `data/`, `tmp/`, `.env`, `*.sqlite3` or `output/` scratch files staged.
2. **Offline tests.** `.venv/bin/python app.py --check` (or the `test-runner` agent).
   Record the real count. If it changed, update the count in CLAUDE.md.
3. **Invariants.** If the diff touches `finance.py`, `dcf.py`, `operating_forecast.py`,
   `fixed_assets.py`, `three_statements.py`, `ledger*.py`, `validation.py`, `ai.py`,
   `approvals.py`, `audit.py`, `cases.py` or `workflow.py`: run the `invariant-reviewer` agent.
   Otherwise skip and say so (conditional verification).
4. **Docs.** If behaviour, commands or modules changed: run the `docs-drift-checker` agent and
   fix what it reports. New architectural choice → new ADR in `docs/DECISIONS.md`.
5. **Evals.** If extraction, retrieval or memo prompts changed, the live eval numbers are stale.
   Do **not** run `--live` evals without the user's OK in this conversation; instead note in
   PROGRESS.md "evals not re-run since <change>".
6. **Secrets.** `git ls-files | grep -E '\.env$|sqlite3|key'` is empty; `.env.example` has
   placeholders only. Never open `.env`.
7. **Handoff.** Run `/handoff` so PROGRESS.md matches reality.
8. **Commit and push.** Commit only when the user asks; push only after asking. Then check
   the `tests` workflow on GitHub (`gh run list --limit 1`). Report its result honestly.
