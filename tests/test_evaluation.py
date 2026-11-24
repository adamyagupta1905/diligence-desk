"""Offline checks for the LLM-only vs pipeline evaluation (no API calls)."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))
from deal_screening.models import FIELDS, MONEY
from deal_screening.validation import normalize_space
import run_eval  # noqa: E402


def perfect_facts(item, chunks):
    """What a flawless extractor would return for this document."""
    period = item["facts"]["period"]["value"]
    currency = item["facts"]["currency"]["value"]
    facts = []
    for field in FIELDS:
        truth = item["facts"][field]
        if truth["needle"] is None:
            facts.append({"field": field, "value": None, "status": "missing", "chunk_id": None,
                          "quote": None, "currency": None, "period": None, "note": ""})
            continue
        chunk = next(c for c in chunks if normalize_space(truth["needle"]) in normalize_space(c["text"]))
        value = truth["value"] if truth["value"] is not None else "Infrastructure business"
        facts.append({"field": field, "value": value, "status": "found", "chunk_id": chunk["id"],
                      "quote": truth["needle"], "currency": currency if field in MONEY else None,
                      "period": period if field in MONEY else None, "note": ""})
    return facts


class FakeClient:
    last_usage = {"input_tokens": 100, "output_tokens": 10}


class FakeExtractor:
    def __init__(self, item, mutate=None):
        self.item, self.mutate = item, mutate

    def extract(self, passages, model):
        facts = perfect_facts(self.item, run_eval.load_chunks(self.item))
        return self.mutate(facts) if self.mutate else facts


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.key = run_eval.load_key()

    def test_answer_key_covers_20_documents_and_hard_cases(self):
        self.assertEqual(len(self.key), 20)
        flags = [d["flags"] for d in self.key]
        self.assertEqual(sum(f["adjusted_only"] for f in flags), 2)
        self.assertEqual(sum(f["net_cash"] for f in flags), 2)
        self.assertEqual(sum(f["units"] == "thousands" for f in flags), 5)
        for d in self.key:
            self.assertEqual(set(d["facts"]), set(FIELDS))

    def test_every_proving_sentence_is_inside_one_passage(self):
        for item in self.key:
            chunks = run_eval.load_chunks(item)
            for field, truth in item["facts"].items():
                if truth["needle"]:
                    with self.subTest(file=item["file"], field=field):
                        self.assertTrue(any(normalize_space(truth["needle"]) in normalize_space(c["text"])
                                            for c in chunks))

    def test_perfect_extractor_scores_full_marks(self):
        results = []
        for item in self.key:
            for approach in ("llm_only", "pipeline_bm25"):
                results.append(run_eval.run_one(approach, item, FakeExtractor(item), FakeClient(), None, "fake"))
        summary = run_eval.summarise(results)
        for row in summary.values():
            done, total = map(int, row["value_accuracy"].split("/"))
            self.assertEqual(done, total)
            done, total = map(int, row["source_accuracy"].split("/"))
            self.assertEqual(done, total)
            self.assertEqual(row["valuation_wrong"], 0)
            self.assertEqual(row["blocked_correctly"], 2)  # the two adjusted-EBITDA-only documents
            self.assertEqual(row["valuation_correct"], 18)

    def test_invented_ebitda_is_counted_as_a_wrong_valuation(self):
        item = next(d for d in self.key if d["flags"]["adjusted_only"])
        chunks = run_eval.load_chunks(item)

        def invent(facts):
            ebitda = next(f for f in facts if f["field"] == "ebitda")
            revenue = next(f for f in facts if f["field"] == "revenue")
            ebitda.update({k: revenue[k] for k in ("status", "chunk_id", "quote", "currency", "period")},
                          value="12300000")
            return facts
        score = run_eval.score_document(item, invent(perfect_facts(item, chunks)), chunks)
        self.assertEqual(score["valuation"], "wrong")
        self.assertFalse(score["fields"]["ebitda"]["value"])

    def test_wrong_source_and_wrong_scale_are_caught(self):
        item = next(d for d in self.key if d["flags"]["units"] == "thousands" and not d["flags"]["adjusted_only"])
        chunks = run_eval.load_chunks(item)
        facts = perfect_facts(item, chunks)
        revenue = next(f for f in facts if f["field"] == "revenue")
        revenue["value"] = str(int(float(revenue["value"]) / 1000))     # forgot "in thousands"
        other = next(c for c in chunks if c["id"] != revenue["chunk_id"])
        ebitda = next(f for f in facts if f["field"] == "ebitda")
        ebitda["chunk_id"] = other["id"]                                  # cites the wrong passage
        score = run_eval.score_document(item, facts, chunks)
        self.assertFalse(score["fields"]["revenue"]["value"])
        self.assertFalse(score["fields"]["ebitda"]["source"])
        self.assertTrue(score["fields"]["ebitda"]["value"])

    def test_value_matching_rules(self):
        self.assertTrue(run_eval.value_matches("revenue", "50000000", "50000000"))
        self.assertTrue(run_eval.value_matches("revenue_growth", "0.09", "0.09"))
        self.assertFalse(run_eval.value_matches("revenue", "50000", "50000000"))
        self.assertTrue(run_eval.value_matches("company_name", "Solara Grid plc", "Solara Grid"))
        self.assertTrue(run_eval.value_matches("period", "FY2025", "2025"))
        self.assertTrue(run_eval.value_matches("currency", "gbp", "GBP"))
        self.assertFalse(run_eval.value_matches("revenue", None, "1"))


if __name__ == "__main__":
    unittest.main()
