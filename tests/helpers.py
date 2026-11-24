"""Known-answer fixtures, NOT an extraction implementation or live AI result."""
from copy import deepcopy
import json
from pathlib import Path

from deal_screening.documents import DocumentReader
from deal_screening.models import MONEY

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "expected_results.json").read_text())
ASSUMPTIONS = {"multiple": 10., "revenue_shock": -.1, "margin_shock_pp": -3., "multiple_shock": -2.}


def fixture(case=CASES[0]):
    data = (ROOT / "sample_documents" / case["file"]).read_bytes()
    pages = DocumentReader.read(data, case["file"])
    chunks = DocumentReader.chunks(pages)
    facts = []
    for field, expected in case["expected"].items():
        status = expected["status"][0]
        needle = {"net_debt": "net debt", "customer_concentration": "largest single customer"}.get(field)
        chunk = next((c for c in chunks if needle and needle in c["text"].lower()), chunks[0])
        facts.append({"field": field, "value": expected["value"], "status": status,
                      "chunk_id": chunk["id"] if status == "found" else None,
                      "quote": chunk["text"] if status == "found" else None,
                      "currency": "USD" if status == "found" and field in MONEY else None,
                      "period": "FY2025" if status == "found" and field in MONEY else None,
                      "note": ""})
    return pages, chunks, facts


class FakeExtractor:
    def __init__(self, facts):
        self.facts = deepcopy(facts)

    def extract(self, passages, model_name):
        return deepcopy(self.facts)


class FakeMemoWriter:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def draft(self, state):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Simulated provider failure")
        return {"observations": [{"text": "Offline test observation.", "references": ["S001"]}] * 3,
                "diligence_questions": ["What requires further diligence?"]}
