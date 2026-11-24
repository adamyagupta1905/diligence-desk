"""Hybrid retrieval: BM25 keyword search + embedding (semantic) search.

Why two searches?
- BM25 matches exact words. It is great for "EBITDA" or "net debt" but misses
  synonyms ("operating profit before depreciation" never says "EBITDA").
- Embeddings turn text into vectors of numbers, so passages with similar
  MEANING sit close together even when the words differ.

We run both, then merge the two rankings with Reciprocal Rank Fusion (RRF):
    score(passage) = 1 / (k + rank_in_bm25) + 1 / (k + rank_in_embeddings)
A passage ranked highly by either method rises to the top. RRF only uses ranks,
so we never have to compare a BM25 score with a cosine similarity directly.

If the embedding call fails (quota, network, model unavailable), the retriever
falls back to BM25 alone and RECORDS that in `last_run`, so the workflow can
show it in the audit trail. Nothing is silently hidden. No Streamlit/Gemini
imports here: the embedder is injected, so tests can use a fake one.
"""
import hashlib
import math
from typing import Protocol

from .documents import PassageRetriever, tokens
from .models import FIELDS


class Embedder(Protocol):
    def embed(self, texts: list[str], task: str) -> list[list[float]]:
        """task is "document" for passages or "query" for search questions."""
        ...


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def rank_order(scores):
    """Indexes sorted best-first; ties keep document order so results are stable."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


class HybridRetriever:
    """Same interface as PassageRetriever, so the workflow does not care which it gets."""

    def __init__(self, embedder, top_k=4, rrf_k=60):
        if top_k < 1:
            raise ValueError("top_k must be positive.")
        self.embedder = embedder
        self.top_k = top_k
        self.rrf_k = rrf_k
        self._cache = {}       # sha256(text) -> vector; avoids re-embedding the same passage
        self.last_run = {}     # what actually happened on the most recent retrieve()

    def _vectors(self, texts, task):
        keys = [hashlib.sha256(f"{task}:{t}".encode()).hexdigest() for t in texts]
        missing = [t for t, k in zip(texts, keys) if k not in self._cache]
        if missing:
            vectors = self.embedder.embed(missing, task)
            if len(vectors) != len(missing):
                raise ValueError("Embedding count does not match input count")
            for text, vector in zip(missing, vectors):
                self._cache[hashlib.sha256(f"{task}:{text}".encode()).hexdigest()] = vector
        return [self._cache[k] for k in keys]

    def orders(self, chunks, queries):
        """For each query: (keyword-only order, final order), passage indexes best-first.

        Also records in self.last_run whether embeddings worked or we fell back.
        """
        from rank_bm25 import BM25Okapi
        if not chunks:
            raise ValueError("The document contains no usable passages.")
        bm25 = BM25Okapi([tokens(c["text"]) or ["empty"] for c in chunks])
        try:
            chunk_vectors = self._vectors([c["text"] for c in chunks], "document")
            query_vectors = self._vectors(list(queries), "query")
            method, error = "hybrid (BM25 + embeddings, RRF)", ""
        except Exception as exc:  # fall back, but say so
            chunk_vectors = query_vectors = None
            method = "BM25 keyword retrieval (embedding fallback)"
            error = type(exc).__name__ + (f": {exc}"[:200] if str(exc) else "")
        self.last_run = {"method": method, "error": error}

        result = []
        for qi, query in enumerate(queries):
            keyword_scores = [float(s) for s in bm25.get_scores(tokens(query))]
            keyword_order = rank_order(keyword_scores)
            if chunk_vectors is None:
                result.append((keyword_order, keyword_order))
                continue
            semantic_scores = [cosine(query_vectors[qi], v) for v in chunk_vectors]
            fused = [0.0] * len(chunks)
            # Only passages that actually MATCH get a vote. If no word matches,
            # BM25's "ranking" is just document order and must not count.
            for scores in (keyword_scores, semantic_scores):
                for position, i in enumerate(rank_order(scores)):
                    if scores[i] > 0:
                        fused[i] += 1 / (self.rrf_k + position + 1)
            result.append((keyword_order, rank_order(fused)))
        return result

    def rank(self, chunks, query):
        """Passage IDs for one query, best match first (used by the benchmark)."""
        return [chunks[i]["id"] for i in self.orders(chunks, [query])[0][1]]

    def retrieve(self, chunks, queries=None, top_k=None):
        top_k = top_k or self.top_k
        queries = list(FIELDS.values() if queries is None else queries)
        chosen, semantic_only = set(), set()
        for keyword_order, final_order in self.orders(chunks, queries):
            picked = set(final_order[:top_k])
            chosen |= picked
            # Passages keyword search alone would NOT have picked: the value embeddings add.
            semantic_only |= picked - set(keyword_order[:top_k])
        self.last_run.update({"queries": len(queries), "selected": len(chosen),
                              "added_by_embeddings": sorted(chunks[i]["id"] for i in semantic_only)})
        return [c for i, c in enumerate(chunks) if i in chosen]


def describe(retriever):
    """Human-readable record of the last retrieval, for the audit trail."""
    run = getattr(retriever, "last_run", None)
    if run:
        return dict(run)
    return {"method": "BM25 keyword retrieval", "error": ""}


def make_retriever(embedder=None, mode="hybrid", top_k=4):
    """mode='bm25' forces keyword-only search (no embedding API calls)."""
    if mode == "bm25" or embedder is None:
        return PassageRetriever(top_k=top_k)
    return HybridRetriever(embedder, top_k=top_k)
