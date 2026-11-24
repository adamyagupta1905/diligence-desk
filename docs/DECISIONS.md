# Architecture decisions

Short records of choices that shape the system. Newest at the bottom.

## ADR-001: Deterministic maths, probabilistic reading
- **Date:** 2026-11-23 (recorded; in place since v1)
- **Decision:** Gemini only extracts, labels and drafts. Every number in a valuation or memo is
  computed in Python, and memo claims must reference `CALC:` outputs.
- **Alternatives:** let the model compute ratios and DCFs inside the memo prompt.
- **Why:** model arithmetic fails silently and plausibly; a wrong EV that looks right is the most
  harmful error in screening. Python results are reproducible and testable.
- **Revisit if:** never for calculations; only the drafting scope might widen.

## ADR-002: Validate analyst input before resuming the graph
- **Date:** 2026-11-23 (recorded; in place since v1)
- **Decision:** reject invalid analyst input before `Command(resume=...)`.
- **Alternatives:** validate inside the resumed node and loop back.
- **Why:** LangGraph caches interrupt answers, so a bad answer passed to resume can be replayed
  even after the analyst corrects it.
- **Revisit if:** LangGraph changes interrupt-answer caching semantics.

## ADR-003: No vector database
- **Date:** 2026-11-23 (recorded; in place since v1)
- **Decision:** in-memory BM25 + embedding cosine search fused with RRF.
- **Alternatives:** pgvector, Chroma, FAISS index on disk.
- **Why:** documents are capped at 30 pages (~100 passages); in-memory search takes
  milliseconds. A vector store adds a service to run with no measurable gain.
- **Revisit if:** document cap rises well beyond ~1,000 passages or search spans many deals.

## ADR-004: Revisions, not edits
- **Date:** 2026-11-23 (recorded; in place since v1)
- **Decision:** any content change creates an immutable new revision; approvals are bound to a
  SHA-256 fingerprint of the content they approved.
- **Alternatives:** edit in place and reset an "approved" flag.
- **Why:** an approval must always point at exactly what was approved; audit history survives.
- **Revisit if:** never weaken; storage format may change.

## ADR-005: Repo memory in CLAUDE.md + PROGRESS.md + git, not zip bundles
- **Date:** 2026-11-23
- **Decision:** always-true facts in `CLAUDE.md`, current state in `PROGRESS.md`, workflows in
  `.claude/skills/`, history in git.
- **Alternatives:** zip "migration bundles" of the repo and chat notes.
- **Why:** a fresh session needs current repo context, not a copied snapshot that goes stale.
- **Revisit if:** the project splits into several repos.

## ADR-006: Claude agent tooling is read-only; PR review is opt-in
- **Date:** 2026-11-23
- **Decision:** project subagents (`.claude/agents/`) get Bash/Read/Grep/Glob only; the main
  session makes every edit. The GitHub Claude review runs only on PRs labelled `claude-review`,
  with `contents: read`.
- **Alternatives:** review every PR push automatically; let Claude fix labelled issues with write access.
- **Why:** one writer keeps changes reviewable; each review is a paid API call, and write access
  to the repo should be granted deliberately.
- **Revisit if:** the repo gets collaborators and review volume justifies automatic runs.
