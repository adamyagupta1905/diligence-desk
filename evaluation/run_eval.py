"""Compare LLM-only extraction with the retrieval pipeline on 20 synthetic documents.

Approaches (both use the SAME extraction prompt and schema from ai.py):
  llm_only   paste the whole document into Gemini ("why not just paste the PDF?")
  pipeline   hybrid search → top 4 passages per fact → Gemini (what the app does)
  pipeline_bm25  (optional) keyword-only search → Gemini

For each document the answer key (answer_key.json) gives the true value and the
exact sentence that proves it. We measure:
  value accuracy   right number/text, or correctly "missing" when absent
  source accuracy  the quote really exists in the cited passage AND that
                   passage contains the proving sentence
  valuation        EV = 10 × EBITDA, equity = EV − net debt, computed by Python
                   only when the facts pass validation, compared with the truth:
                   correct / WRONG (the dangerous case) / correctly blocked /
                   blocked although computable (safe but unhelpful)
  speed and tokens, plus cost if you pass your model's prices

Nothing runs without --live. Results are saved to evaluation/results/.

From the repository root:
    .venv/bin/python evaluation/run_eval.py --live --limit 2      # smoke test
    .venv/bin/python evaluation/run_eval.py --live                # all 20
    .venv/bin/python evaluation/run_eval.py --live --price-in 0.30 --price-out 2.50
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from deal_screening.ai import FactExtractor                      # noqa: E402
from deal_screening.documents import DocumentReader, PassageRetriever  # noqa: E402
from deal_screening.models import FIELDS, NUMERIC                # noqa: E402
from deal_screening.retrieval import HybridRetriever, describe   # noqa: E402
from deal_screening.validation import InputValidator, normalize_space  # noqa: E402

MULTIPLE = 10.0
APPROACHES = ("llm_only", "pipeline", "pipeline_bm25")


def load_key():
    return json.loads((HERE / "answer_key.json").read_text())


def load_chunks(item):
    path = HERE / "documents" / item["file"]
    return DocumentReader.chunks(DocumentReader.read(path.read_bytes(), path.name))


# ------------------------------------------------------------------ scoring
def value_matches(field, got, truth):
    if got in (None, ""):
        return False
    got = str(got).strip()
    if field in NUMERIC:
        try:
            g, t = float(got), float(truth)
        except ValueError:
            return False
        return math.isclose(g, t, rel_tol=0.005, abs_tol=1e-9)
    if field == "company_name":
        return truth.lower() in got.lower()
    if field == "period":
        return truth in got
    return got.upper() == truth.upper()


def score_fact(field, fact, truth, chunks):
    """Return (value_correct, source_correct or None when not applicable)."""
    expected_missing = truth["value"] is None and not truth.get("free_text")
    found = fact.get("status") == "found" and fact.get("value") not in (None, "")
    if expected_missing:
        return (not found), None
    if truth.get("free_text"):
        value_ok = found
    else:
        value_ok = found and value_matches(field, fact.get("value"), truth["value"])
    lookup = {c["id"]: c["text"] for c in chunks}
    cited = normalize_space(lookup.get(fact.get("chunk_id"), ""))
    quote = normalize_space(fact.get("quote"))
    source_ok = bool(found and cited and quote and quote in cited
                     and normalize_space(truth["needle"]) in cited)
    return value_ok, source_ok


def truth_equity(item):
    facts = item["facts"]
    if facts["ebitda"]["value"] is None:
        return None
    return MULTIPLE * float(facts["ebitda"]["value"]) - float(facts["net_debt"]["value"])


def score_valuation(item, facts, chunks):
    issues, company = InputValidator.facts(facts, chunks, method="ev_ebitda")
    truth = truth_equity(item)
    if issues:
        return "blocked_correctly" if truth is None else "blocked_unnecessarily"
    equity = MULTIPLE * company["ebitda"] - company["net_debt"]
    if truth is None:
        return "wrong"  # produced a valuation the document cannot support
    return "correct" if math.isclose(equity, truth, rel_tol=0.005) else "wrong"


def score_document(item, facts, chunks):
    by_field = {f["field"]: f for f in facts}
    rows = {}
    for field in FIELDS:
        rows[field] = score_fact(field, by_field.get(field, {}), item["facts"][field], chunks)
    return {"fields": {f: {"value": v, "source": s} for f, (v, s) in rows.items()},
            "valuation": score_valuation(item, facts, chunks)}


# ------------------------------------------------------------------ running
def run_one(approach, item, extractor, client, embedder, model):
    chunks = load_chunks(item)
    start = time.perf_counter()
    retrieval = {}
    if approach == "llm_only":
        passages = chunks
    else:
        retriever = HybridRetriever(embedder) if approach == "pipeline" else PassageRetriever()
        passages = retriever.retrieve(chunks)
        retrieval = describe(retriever)
    try:
        facts = extractor.extract(passages, model)
        error = ""
    except Exception as exc:
        from deal_screening.llm import safe_api_error
        facts, error = [], safe_api_error(exc)
    seconds = time.perf_counter() - start
    usage = dict(getattr(client, "last_usage", {}) or {})
    result = {"file": item["file"], "approach": approach, "seconds": round(seconds, 2),
              "passages_sent": len(passages), "passages_total": len(chunks),
              "usage": usage, "error": error, "retrieval": retrieval, "facts": facts}
    if not error:
        result["score"] = score_document(item, facts, chunks)
    return result


def summarise(results, price_in=None, price_out=None):
    table = {}
    for approach in sorted({r["approach"] for r in results}):
        rs = [r for r in results if r["approach"] == approach]
        ok = [r for r in rs if "score" in r]
        values = [f["value"] for r in ok for f in r["score"]["fields"].values()]
        sources = [f["source"] for r in ok for f in r["score"]["fields"].values() if f["source"] is not None]
        vals = [r["score"]["valuation"] for r in ok]
        tin = sum(r["usage"].get("input_tokens", 0) for r in rs)
        tout = sum(r["usage"].get("output_tokens", 0) for r in rs)
        row = {"documents": len(rs), "errors": len(rs) - len(ok),
               "value_accuracy": f"{sum(values)}/{len(values)}",
               "source_accuracy": f"{sum(sources)}/{len(sources)}",
               "valuation_correct": vals.count("correct"),
               "valuation_wrong": vals.count("wrong"),
               "blocked_correctly": vals.count("blocked_correctly"),
               "blocked_unnecessarily": vals.count("blocked_unnecessarily"),
               "avg_seconds": round(sum(r["seconds"] for r in rs) / max(len(rs), 1), 2),
               "avg_passages_sent": round(sum(r["passages_sent"] for r in rs) / max(len(rs), 1), 1),
               "input_tokens": tin, "output_tokens": tout}
        if price_in is not None and price_out is not None:
            row["cost_usd"] = round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4)
        if approach == "pipeline":
            row["embedding_fallbacks"] = sum(1 for r in rs if r["retrieval"].get("error"))
        table[approach] = row
    return table


def per_field(results):
    out = {}
    for approach in sorted({r["approach"] for r in results}):
        ok = [r for r in results if r["approach"] == approach and "score" in r]
        out[approach] = {f: f"{sum(r['score']['fields'][f]['value'] for r in ok)}/{len(ok)}" for f in FIELDS}
    return out


def rescore(path, price_in=None, price_out=None):
    """Apply today's validation and scoring to answers saved from an earlier live run."""
    data = json.loads(path.read_text())
    key = {item["file"]: item for item in load_key()}
    for r in data["results"]:
        if not r["error"]:
            r["score"] = score_document(key[r["file"]], r["facts"], load_chunks(key[r["file"]]))
    data["summary"] = summarise(data["results"], price_in, price_out)
    data["per_field"] = per_field(data["results"])
    out = path.with_name(path.stem + "_rescored.json")
    out.write_text(json.dumps(data, indent=2))
    print(json.dumps(data["summary"], indent=2))
    print(f"\nSaved {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Authorize real Gemini requests")
    parser.add_argument("--limit", type=int, help="Only the first N documents (smoke test)")
    parser.add_argument("--approaches", default="llm_only,pipeline",
                        help=f"Comma list from {', '.join(APPROACHES)}")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens for your model")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens for your model")
    parser.add_argument("--rescore", help="Re-score a saved results file with the current code (no API calls)")
    args = parser.parse_args()
    if args.rescore:
        rescore(Path(args.rescore), args.price_in, args.price_out)
        return
    if not args.live:
        parser.error("This calls Gemini. Add --live to authorize it (see the docstring for cost notes).")
    approaches = [a.strip() for a in args.approaches.split(",")]
    if set(approaches) - set(APPROACHES):
        parser.error(f"Unknown approach. Choose from {APPROACHES}.")
    from dotenv import load_dotenv
    from deal_screening.llm import GeminiClient, GeminiEmbedder
    load_dotenv(HERE.parent / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        parser.error("Set GEMINI_API_KEY in .env.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    client = GeminiClient(key)
    extractor = FactExtractor(client)
    embedder = GeminiEmbedder(key, os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"))
    items = load_key()[:args.limit] if args.limit else load_key()
    results = []
    for n, item in enumerate(items, 1):
        for approach in approaches:
            client.last_usage = {}
            r = run_one(approach, item, extractor, client, embedder, model)
            status = r["error"] or f"{sum(f['value'] for f in r['score']['fields'].values())}/9 values, valuation {r['score']['valuation']}"
            print(f"[{n}/{len(items)}] {item['file']:<40} {approach:<14} {r['seconds']:>6.1f}s  {status}", flush=True)
            results.append(r)
    summary = summarise(results, args.price_in, args.price_out)
    fields = per_field(results)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"run_{stamp}.json"
    out.write_text(json.dumps({"model": model, "time_utc": stamp, "summary": summary,
                               "per_field": fields, "results": results}, indent=2))
    print("\nSUMMARY")
    print(json.dumps(summary, indent=2))
    print("\nVALUE ACCURACY BY FIELD")
    print(json.dumps(fields, indent=2))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
