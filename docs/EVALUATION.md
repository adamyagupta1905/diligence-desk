# Evaluation

Two evaluations measure the system against labelled answer keys. All test
documents are fictional and generated or written for this purpose. Raw results
are committed in `evaluation/results/`, and failures are reported alongside successes.

## 1. Retrieval benchmark

`evaluation/benchmark_retrieval.py` checks whether search finds the passage holding each
answer in a 13-page fictional annual review (`sample_documents/16_long_annual_review_synonyms.txt`). The review is written
with synonyms on purpose: "turnover" for revenue, "borrowings less cash" for
net debt, "operating profit before depreciation" for EBITDA.

| Search | Answer ranked 1st | In top 2 | In top 4 (what the AI sees) | MRR |
|---|---|---|---|---|
| BM25 keywords | 2 / 9 | 4 / 9 | 8 / 9 | 0.481 |
| Hybrid (BM25 + `gemini-embedding-001`) | 3 / 9 | **8 / 9** | 8 / 9 | **0.621** |

*Measured 2026-11-22 on one run.*

**What improved:** hybrid search put the right passage in the top 2 for 8 of 9
facts (up from 4), and MRR rose by 29%. Currency and customer concentration
moved to 1st place, because embeddings matched "pounds sterling" and
"biggest client" despite no shared words.

**What still fails:** both methods miss **revenue growth**. The answer ("an
increase of 9% compared with the previous twelve months") ranks 13th with BM25
and 11th with hybrid. A decoy, "market growth of around 4% per year", is
closer to the query in both wording and meaning. In the app that fact would
come back *missing* (never invented), and the analyst would need to add it.
Company name also slipped from 1st to 2nd. The queries were deliberately **not**
tuned to this document, which would overfit one test; the 20-document
evaluation below is the broader check.

```bash
.venv/bin/python evaluation/benchmark_retrieval.py          # keywords only, free
.venv/bin/python evaluation/benchmark_retrieval.py --live   # adds hybrid, tiny embedding cost
```

MRR (mean reciprocal rank) averages 1/rank of the right passage: 1.0 means
always first. One small labelled document is an illustration, not proof of
general accuracy.

## 2. Extraction evaluation: LLM-only vs the pipeline

`evaluation/` compares two approaches on **20 synthetic infrastructure annual
reviews** (solar, toll roads, water, ports, data centres…; 12–15 pages each),
using the same prompt and schema:

- **LLM-only:** the whole document is pasted into Gemini.
- **Pipeline:** hybrid search → selected passages → Gemini (what the app does).

The documents include hard cases: 5 in thousands with the scale stated on another
page, 2 net-cash companies, 2 reporting only *adjusted* EBITDA (reported EBITDA
must stay missing), absent optional facts, and decoys (prior-year turnover,
market growth, gross borrowings, margin targets). The answer key holds the true
value and the exact proving sentence for all 180 facts.

**Live run, 2026-11-23** (`gemini-3.8-flash`, one run, results in `evaluation/results/`):

| | LLM-only | Pipeline |
|---|---|---|
| Calls completed | 18 / 20 | 17 / 20 |
| Facts correct, including correctly "missing" | **162 / 162** | **153 / 153** |
| Citations correct (quote exists *and* the passage proves it) | 154 / 154 | 145 / 145 |
| Valuation correct | 16 | 14 |
| **Valuation wrong** (the dangerous case) | **0** | **0** |
| Correctly blocked (adjusted-EBITDA-only documents) | 2 | 2 |
| Blocked although computable | 0 | 1 |
| Median time per document | 26 s | 28 s |
| Passages sent / input tokens per call | 14 / ~1,670 | 11.6 / ~1,450 |
| Output tokens per call (mostly model "thinking") | ~3,140 | ~3,370 |

Valuation rows are after the bug fix described below, re-scored from the same
saved answers with `run_eval.py --rescore` (no new API calls).

### Findings

1. **On clean 12–15 page documents, retrieval adds no accuracy.** Gemini got
   every fact right either way, including thousands, net cash and refusing
   adjusted EBITDA. The pipeline sent ~83% of passages (9 questions × top 4
   covers most of a short document), so it saved only ~13% of input tokens, and
   output tokens dominate cost anyway. The pipeline's case rests on **auditable
   citations, documents too long to paste, and lower cost at scale**, not on
   accuracy at this size.
2. **The evaluation found a real bug in the app.** The prompt tells the model
   that net debt is a balance *at the period end* ("31 December 2024"), but the
   validator demanded the exact period text ("year ended 31 December 2024").
   Correct answers were blocked in **25 of 35** completed runs (only 4 and 2
   correct valuations before the fix). The validator now accepts a period-end
   date that matches the reporting period. A bare "2025" still needs analyst
   confirmation because it doesn't say when the year ends; that is the one
   remaining block, and it is deliberate.
3. **Timeouts: 5 of 40 calls (12.5%) failed** at the old 60-second limit. The
   timeout is now 120 s. Failed calls returned visible errors; no facts were invented.
4. **Zero wrong valuations.** No approach produced a confident wrong number.
   Every failure was a visible error or a block.

**Limits of this test:** synthetic documents are cleaner than real filings (no
tables, no scanned pages, no footnotes), and this is one run of one model. The
next step is real public filings and longer documents, where retrieval should matter more.

```bash
.venv/bin/python evaluation/run_eval.py --live          # ~40 calls, well under $1
.venv/bin/python evaluation/run_eval.py --rescore evaluation/results/<file>.json
```
