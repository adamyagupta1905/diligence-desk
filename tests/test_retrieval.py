"""Hybrid retrieval tests. A fake embedder stands in for Gemini, so no API calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening.documents import PassageRetriever
from deal_screening.retrieval import HybridRetriever, cosine, make_retriever


class ConceptEmbedder:
    """Maps words to 'concepts', so synonyms land on the same vector dimension.

    This imitates what a real embedding model does: 'operating profit before
    depreciation' and 'EBITDA' mean the same thing, so they get similar vectors.
    """
    CONCEPTS = [
        {"ebitda", "depreciation", "amortisation", "amortization", "operating"},
        {"revenue", "sales", "turnover"},
        {"debt", "borrowings", "loans"},
        {"weather", "office", "parking", "staff", "picnic"},
    ]

    def __init__(self):
        self.calls = []

    def embed(self, texts, task):
        self.calls.append((task, len(texts)))
        out = []
        for text in texts:
            words = set(text.lower().replace(",", " ").replace(".", " ").split())
            out.append([float(len(words & concept)) for concept in self.CONCEPTS])
        return out


class BrokenEmbedder:
    def embed(self, texts, task):
        raise ConnectionError("quota exhausted")


CHUNKS = [
    {"id": "S001", "page": 1, "text": "The office parking area was repaved and the staff picnic was held in June."},
    {"id": "S002", "page": 1, "text": "Turnover for the year reached 50 million."},
    {"id": "S003", "page": 2, "text": "Operating profit before depreciation and amortisation was 8 million."},
    {"id": "S004", "page": 2, "text": "Bank borrowings at year end were 12 million."},
    {"id": "S005", "page": 3, "text": "The weather in the region was mild and the office moved floors."},
]


class HybridRetrieverTests(unittest.TestCase):
    def test_cosine_basics(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine([0, 0], [1, 1]), 0.0)  # no division by zero

    def test_embeddings_find_synonym_that_keywords_miss(self):
        query = ["EBITDA"]
        keyword = PassageRetriever(top_k=1).retrieve(CHUNKS, queries=query)
        # The word "EBITDA" appears nowhere, so BM25 just returns the first passage.
        self.assertNotEqual([c["id"] for c in keyword], ["S003"])
        retriever = HybridRetriever(ConceptEmbedder(), top_k=1)
        hybrid = retriever.retrieve(CHUNKS, queries=query)
        self.assertEqual([c["id"] for c in hybrid], ["S003"])
        self.assertIn("S003", retriever.last_run["added_by_embeddings"])
        self.assertTrue(retriever.last_run["method"].startswith("hybrid"))

    def test_exact_keyword_match_still_wins(self):
        hybrid = HybridRetriever(ConceptEmbedder(), top_k=1).retrieve(CHUNKS, queries=["bank borrowings"])
        self.assertEqual([c["id"] for c in hybrid], ["S004"])

    def test_results_keep_document_order_page_and_ids(self):
        passages = HybridRetriever(ConceptEmbedder(), top_k=1).retrieve(
            CHUNKS, queries=["debt loans", "revenue sales", "EBITDA"])
        self.assertEqual([c["id"] for c in passages], ["S002", "S003", "S004"])
        self.assertEqual([c["page"] for c in passages], [1, 2, 2])

    def test_failure_falls_back_to_bm25_and_says_so(self):
        retriever = HybridRetriever(BrokenEmbedder(), top_k=1)
        passages = retriever.retrieve(CHUNKS, queries=["bank borrowings"])
        expected = PassageRetriever(top_k=1).retrieve(CHUNKS, queries=["bank borrowings"])
        self.assertEqual(passages, expected)
        self.assertIn("fallback", retriever.last_run["method"])
        self.assertIn("quota exhausted", retriever.last_run["error"])

    def test_passage_vectors_are_cached_between_calls(self):
        embedder = ConceptEmbedder()
        retriever = HybridRetriever(embedder)
        retriever.retrieve(CHUNKS, queries=["revenue"])
        retriever.retrieve(CHUNKS, queries=["revenue", "debt"])
        documents = sum(n for task, n in embedder.calls if task == "document")
        self.assertEqual(documents, len(CHUNKS))  # passages embedded once only

    def test_wrong_vector_count_triggers_visible_fallback(self):
        class ShortEmbedder:
            def embed(self, texts, task):
                return [[1.0]]
        retriever = HybridRetriever(ShortEmbedder())
        retriever.retrieve(CHUNKS, queries=["revenue"])
        self.assertIn("fallback", retriever.last_run["method"])

    def test_empty_document_and_bad_top_k_rejected(self):
        with self.assertRaises(ValueError):
            HybridRetriever(ConceptEmbedder()).retrieve([])
        with self.assertRaises(ValueError):
            HybridRetriever(ConceptEmbedder(), top_k=0)

    def test_factory_modes(self):
        self.assertIsInstance(make_retriever(ConceptEmbedder(), "hybrid"), HybridRetriever)
        self.assertIsInstance(make_retriever(ConceptEmbedder(), "bm25"), PassageRetriever)
        self.assertIsInstance(make_retriever(None, "hybrid"), PassageRetriever)  # no key -> no embeddings


class WorkflowRetrievalAuditTests(unittest.TestCase):
    def test_workflow_records_actual_retrieval_method(self):
        from deal_screening.workflow import ScreeningWorkflow
        from helpers import FakeExtractor, FakeMemoWriter
        pages = [{"page": 1, "text": CHUNKS[1]["text"] + " " + CHUNKS[2]["text"]}]
        for retriever, expected in [(HybridRetriever(ConceptEmbedder()), "hybrid"),
                                    (HybridRetriever(BrokenEmbedder()), "fallback"),
                                    (PassageRetriever(), "BM25")]:
            workflow = ScreeningWorkflow(FakeExtractor([]), FakeMemoWriter(), retriever=retriever)
            update = workflow.retrieve({"pages": pages, "audit": []})
            self.assertIn(expected, update["retrieval"]["method"])
            self.assertIn(expected, update["audit"][-1]["method"])


if __name__ == "__main__":
    unittest.main()
