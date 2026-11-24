"""Offline checks for the retrieval benchmark harness (no API calls)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
from benchmark_retrieval import LABELS, answer_ids, evaluate, load_chunks
from deal_screening.documents import PassageRetriever
from deal_screening.models import FIELDS
from deal_screening.retrieval import HybridRetriever


class OracleEmbedder:
    """A perfect fake embedder: each query points straight at its labelled answer.

    Real embeddings are not perfect. This only proves the fusion lets a strong
    meaning signal rescue a passage that keyword search ranks last.
    """
    def embed(self, texts, task):
        fields = list(LABELS["answers"])
        vectors = []
        for text in texts:
            if task == "query":
                hits = [f for f in fields if FIELDS[f] == text]
            else:
                hits = [f for f in fields if LABELS["answers"][f] in text]
            vectors.append([1.0 if f in hits else 0.0 for f in fields])
        return vectors


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.chunks = load_chunks()

    def test_every_label_points_to_a_real_passage(self):
        ids = answer_ids(self.chunks)
        self.assertEqual(set(ids), set(FIELDS))
        self.assertTrue(all(ids.values()))

    def test_bm25_baseline_is_stable(self):
        rows, summary = evaluate(PassageRetriever(), self.chunks)
        # Recorded baseline; if this changes, retrieval behaviour changed.
        self.assertEqual(summary["hit@4"], 8)
        self.assertEqual(next(r for r in rows if r["field"] == "revenue_growth")["rank"], 13)

    def test_rank_lists_every_passage_once(self):
        for retriever in (PassageRetriever(), HybridRetriever(OracleEmbedder())):
            order = retriever.rank(self.chunks, FIELDS["revenue"])
            self.assertEqual(sorted(order), sorted(c["id"] for c in self.chunks))

    def test_strong_meaning_signal_rescues_keyword_misses(self):
        _, keyword = evaluate(PassageRetriever(), self.chunks)
        rows, hybrid = evaluate(HybridRetriever(OracleEmbedder()), self.chunks)
        self.assertEqual(hybrid["hit@4"], 9)
        self.assertGreater(hybrid["MRR"], keyword["MRR"])
        self.assertLessEqual(next(r for r in rows if r["field"] == "revenue_growth")["rank"], 4)


if __name__ == "__main__":
    unittest.main()
