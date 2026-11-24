"""Retrieval benchmark: does search find the passage that holds each answer?

Uses a 13-page fictional annual review (sample 16) written with synonyms, such
as "turnover" for revenue and "borrowings less cash" for net debt, so keyword
search can't simply match the query words. For each of the nine fields we know
which passage holds the answer (tests/retrieval_labels.json) and check:

  hit@k  the answer passage is among the top k passages for that field's query
  rank   where the answer passage ranked (1 = first); MRR = average of 1/rank

BM25 runs offline for free. Hybrid needs --live and makes one or two small
Gemini embedding requests (13 passages + 9 queries), costing a fraction of a cent.

From the repository root:
    .venv/bin/python evaluation/benchmark_retrieval.py
    .venv/bin/python evaluation/benchmark_retrieval.py --live
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so "deal_screening" imports work from any folder

from dotenv import load_dotenv
from deal_screening.documents import DocumentReader, PassageRetriever
from deal_screening.models import FIELDS
from deal_screening.retrieval import HybridRetriever, describe

LABELS = json.loads((ROOT / "tests" / "retrieval_labels.json").read_text())


def load_chunks():
    path = ROOT / "sample_documents" / LABELS["document"]
    return DocumentReader.chunks(DocumentReader.read(path.read_bytes(), path.name))


def answer_ids(chunks):
    """Passage IDs that contain each labelled answer."""
    ids = {field: {c["id"] for c in chunks if needle in c["text"]}
           for field, needle in LABELS["answers"].items()}
    missing = [field for field, found in ids.items() if not found]
    if missing:
        raise ValueError(f"Labelled answers not found in any passage: {missing}")
    return ids


def evaluate(retriever, chunks, ks=(1, 2, 4)):
    answers = answer_ids(chunks)
    rows = []
    for field, query in FIELDS.items():
        order = retriever.rank(chunks, query)
        rank = min(order.index(i) + 1 for i in answers[field])
        rows.append({"field": field, "rank": rank, **{f"hit@{k}": rank <= k for k in ks}})
    summary = {f"hit@{k}": sum(r[f"hit@{k}"] for r in rows) for k in ks}
    summary["MRR"] = round(sum(1 / r["rank"] for r in rows) / len(rows), 3)
    return rows, summary


def print_table(name, rows, summary, ks=(1, 2, 4)):
    print(f"\n{name}")
    print(f"{'field':24} {'rank':>4}  " + "  ".join(f"{'hit@' + str(k):>6}" for k in ks))
    for r in rows:
        print(f"{r['field']:24} {r['rank']:>4}  " + "  ".join(f"{'✓' if r[f'hit@{k}'] else '·':>6}" for k in ks))
    n = len(rows)
    print(f"{'TOTAL':24} {'':>4}  " + "  ".join(f"{str(summary[f'hit@{k}']) + '/' + str(n):>6}" for k in ks)
          + f"   MRR {summary['MRR']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Also run hybrid search with real Gemini embeddings")
    args = parser.parse_args()
    chunks = load_chunks()
    print(f"Document: {LABELS['document']} — {len(chunks)} passages, {len(FIELDS)} questions. "
          "The app sends the top 4 per question to the AI.")
    rows, summary = evaluate(PassageRetriever(), chunks)
    print_table("BM25 keyword search", rows, summary)
    if not args.live:
        print("\nAdd --live to compare hybrid search (uses your Gemini key; tiny cost).")
        return
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        parser.error("Set GEMINI_API_KEY in .env.")
    from deal_screening.llm import GeminiEmbedder
    retriever = HybridRetriever(GeminiEmbedder(key, os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")))
    rows, summary = evaluate(retriever, chunks)
    run = describe(retriever)
    if run.get("error"):
        print(f"\nEmbedding call failed, so this is NOT a hybrid result: {run['error']}")
        raise SystemExit(1)
    print_table("Hybrid search (BM25 + Gemini embeddings, RRF)", rows, summary)


if __name__ == "__main__":
    main()
