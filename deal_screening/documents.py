"""Document ingestion and BM25 keyword retrieval (see retrieval.py for hybrid search).
No Gemini or Streamlit dependencies."""
import io
import re
from .models import FIELDS

def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())

class DocumentReader:
    """Read searchable PDF/text documents and preserve page numbers."""

    @staticmethod
    def read(data, name, *, force_ocr=False, ocr_engine=None):
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("Use a document smaller than 10 MB for this first version.")
        if name.lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("Use an unencrypted PDF.")
            if len(reader.pages) > 30:
                raise ValueError("Use at most 30 pages for this first version.")
            pages, ocr_count = [], 0
            for i, page in enumerate(reader.pages):
                # Forced OCR must bypass a broken native text layer entirely.
                text = "" if force_ocr else (page.extract_text() or "")
                sparse = len(re.sub(r"\W", "", text)) < 80
                needs_ocr = force_ocr or (sparse and bool(page.images))
                if needs_ocr:
                    ocr_count += 1
                    if ocr_count > 10:
                        raise ValueError("Use at most 10 OCR pages per document in this local version.")
                    from .ocr import LocalOCREngine
                    result = (ocr_engine or LocalOCREngine()).recognize(data, i)
                    pages.append({"page": i + 1, **result})
                elif text.strip():
                    pages.append({"page": i + 1, "text": text, "extraction_method": "native"})
                else:
                    raise ValueError(f"Page {i + 1} is blank or has no readable content.")
        else:
            if force_ocr:
                raise ValueError("Force OCR applies to PDF files only.")
            pages = [{"page": i + 1, "text": text}
                     for i, text in enumerate(data.decode("utf-8-sig").split("\f"))]
        if not any(p["text"].strip() for p in pages):
            raise ValueError("The document contains no readable text.")
        if len(pages) > 30 or sum(len(p["text"]) for p in pages) > 100_000:
            raise ValueError("Use at most 30 pages and 100,000 characters.")
        return pages

    @staticmethod
    def chunks(pages, size=1100, overlap=180):
        chunks = []
        for page in pages:
            text = page["text"]
            for start in range(0, len(text), size - overlap):
                part = text[start:start + size].strip()
                if part:
                    chunks.append({"id": f"S{len(chunks) + 1:03d}",
                                   "page": page["page"], "text": part,
                                   "extraction_method": page.get("extraction_method", "text")})
        return chunks


class PassageRetriever:
    """Retrieve a union of top passages for each requested financial field."""

    def __init__(self, top_k=4):
        if top_k < 1:
            raise ValueError("top_k must be positive.")
        self.top_k = top_k

    def orders(self, chunks, queries):
        """For each query, passage indexes best-first."""
        from rank_bm25 import BM25Okapi
        if not chunks:
            raise ValueError("The document contains no usable passages.")
        index = BM25Okapi([tokens(c["text"]) or ["empty"] for c in chunks])
        result = []
        for query in queries:
            scores = index.get_scores(tokens(query))
            result.append(sorted(range(len(chunks)), key=lambda i: (-float(scores[i]), i)))
        return result

    def rank(self, chunks, query):
        """Passage IDs for one query, best match first (used by the benchmark)."""
        return [chunks[i]["id"] for i in self.orders(chunks, [query])[0]]

    def retrieve(self, chunks, queries=None, top_k=None):
        top_k = top_k or self.top_k
        queries = list(FIELDS.values() if queries is None else queries)
        chosen = set()
        for order in self.orders(chunks, queries):
            chosen.update(order[:top_k])
        return [c for i, c in enumerate(chunks) if i in chosen]
