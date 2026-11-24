"""Optional live extraction evaluation against known-answer upload fixtures.

No requests occur unless --live is supplied. Each case sends ONE extraction
request. No memo generation, automatic analyst approval, or production data.

From the repository root:
    .venv/bin/python evaluation/evaluate_extraction.py --live --case 02
    .venv/bin/python evaluation/evaluate_extraction.py --live --case all --retrieval hybrid
"""
import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so "deal_screening" imports work from any folder

from dotenv import load_dotenv
from deal_screening.ai import FactExtractor
from deal_screening.documents import DocumentReader
from deal_screening.llm import GeminiClient, GeminiEmbedder, safe_api_error
from deal_screening.retrieval import describe, make_retriever
from deal_screening.models import NUMERIC
from deal_screening.validation import InputValidator



def compare(case, facts, chunks):
    failures = []
    actual = {f["field"]: f for f in facts}
    for field, expected in case["expected"].items():
        fact = actual[field]
        if fact["status"] not in expected["status"]:
            failures.append(f"{field}: status {fact['status']!r}; expected {expected['status']}")
        value, wanted = fact["value"], expected["value"]
        if value is not None and wanted is not None and field in NUMERIC:
            try:
                equal = Decimal(value) == Decimal(wanted)
            except (InvalidOperation, TypeError):
                equal = False
        else:
            equal = value == wanted
        if not equal:
            failures.append(f"{field}: value {value!r}; expected {wanted!r}")
    issues, _ = InputValidator.facts(facts, chunks)
    if bool(issues) != case["blocked"]:
        failures.append(f"Expected calculation blocked={case['blocked']}; validation issues={issues}")
    # Even a blocked case must not contain invented quotes in its other found facts.
    unexpected_issues = [issue for issue in issues
                         if not any(issue.startswith(field + ":") for field in case["blocked_fields"])]
    failures.extend(unexpected_issues)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize real Gemini requests using .env")
    parser.add_argument("--case", required=True, help="Filename prefix, e.g. 01, 02, or all")
    parser.add_argument("--retrieval", choices=["bm25", "hybrid"], default="bm25",
                        help="Search method to evaluate. hybrid also makes embedding requests.")
    args = parser.parse_args()
    if not args.live:
        parser.error("This evaluates the live model. Supply --live to authorize API usage, or run app.py --check for free offline tests.")
    cases = json.loads((ROOT / "tests" / "expected_results.json").read_text())
    selected = [c for c in cases if args.case == "all" or c["file"].startswith(args.case)]
    if not selected:
        parser.error("No matching case. Choose 01 through 08, or all.")
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        parser.error("Set GEMINI_API_KEY in .env.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    extractor = FactExtractor(GeminiClient(key))
    embedder = GeminiEmbedder(key, os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"))
    retriever = make_retriever(embedder, args.retrieval)
    failed = 0
    for case in selected:
        print(f"Evaluating {case['file']} with {model}, retrieval={args.retrieval}…", flush=True)
        try:
            data = (ROOT / "sample_documents" / case["file"]).read_bytes()
            pages = DocumentReader.read(data, case["file"])
            chunks = DocumentReader.chunks(pages)
            passages = retriever.retrieve(chunks)
            run = describe(retriever)
            print(f"  {len(passages)}/{len(chunks)} passages via {run['method']}"
                  + (f" ({run['error']})" if run.get("error") else ""))
            facts = extractor.extract(passages, model)
            failures = compare(case, facts, chunks)
        except Exception as exc:
            failures = [safe_api_error(exc)]
        print("FAIL" if failures else "PASS")
        for failure in failures:
            print(" - " + failure)
        failed += bool(failures)
    print(f"{len(selected) - failed}/{len(selected)} live extraction cases passed.")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
