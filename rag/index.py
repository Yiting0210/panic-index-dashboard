"""FAISS vector index over FOMC chunks with as-of pre-filtering.

Point-in-time filtering happens *inside* the FAISS search through an ID
selector, rather than by over-fetching and dropping late documents
afterwards. Over-fetching can silently return fewer than k results, or none,
when the newest documents dominate the similarity ranking.
"""
import json
from datetime import date
from pathlib import Path

import faiss
import numpy as np

from .chunking import chunk_documents, embedding_text, load_documents
from .lexical import BM25, reciprocal_rank_fusion
from .query import month_mentions

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "fomc"
DOCUMENTS_PATH = DATA_DIR / "documents.jsonl"
INDEX_DIR = DATA_DIR / "index"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
SEARCH_MODES = ("hybrid", "dense", "bm25")
CANDIDATES_PER_RETRIEVER = 50


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed (no API key, runs on CPU)."""

    def __init__(self, model_name=DEFAULT_MODEL):
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts):
        return np.asarray(list(self._model.embed(texts)), dtype="float32")

    def embed_query(self, text):
        return np.asarray(list(self._model.query_embed(text)), dtype="float32")


def _normalize(vectors):
    vectors = np.ascontiguousarray(vectors, dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


class FedDocIndex:
    def __init__(self, chunks, index, embedder, model_name):
        self.chunks = chunks
        self.index = index
        self.embedder = embedder
        self.model_name = model_name
        self._published = np.array(
            [date.fromisoformat(c["published_at"]).toordinal() for c in chunks]
        )
        self._doc_types = np.array([c["doc_type"] for c in chunks])
        self._meeting_months = np.array([c["meeting_date"][:7] for c in chunks])
        self._bm25 = None

    @property
    def bm25(self):
        if self._bm25 is None:
            self._bm25 = BM25([embedding_text(c) for c in self.chunks])
        return self._bm25

    @classmethod
    def build(cls, documents, embedder):
        chunks = list(chunk_documents(documents))
        vectors = _normalize(embedder.embed_documents([embedding_text(c) for c in chunks]))
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        return cls(chunks, index, embedder, embedder.model_name)

    def save(self, directory=INDEX_DIR):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(directory / "chunks.faiss"))
        with (directory / "chunks.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for chunk in self.chunks:
                fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        manifest = {
            "embedding_model": self.model_name,
            "dimension": self.index.d,
            "chunks": len(self.chunks),
            "documents": len({c["doc_id"] for c in self.chunks}),
            "first_published": min(c["published_at"] for c in self.chunks),
            "last_published": max(c["published_at"] for c in self.chunks),
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    @classmethod
    def load(cls, directory=INDEX_DIR, embedder=None):
        directory = Path(directory)
        manifest = json.loads((directory / "manifest.json").read_text())
        if embedder is None:
            embedder = FastEmbedEmbedder(manifest["embedding_model"])
        elif embedder.model_name != manifest["embedding_model"]:
            raise ValueError(
                f"Index was built with {manifest['embedding_model']}, "
                f"got embedder {embedder.model_name}."
            )
        index = faiss.read_index(str(directory / "chunks.faiss"))
        with (directory / "chunks.jsonl").open(encoding="utf-8") as fh:
            chunks = [json.loads(line) for line in fh if line.strip()]
        return cls(chunks, index, embedder, manifest["embedding_model"])

    def eligible_ids(self, as_of, doc_type=None):
        mask = self._published <= as_of.toordinal()
        if doc_type is not None:
            mask &= self._doc_types == doc_type
        return np.flatnonzero(mask).astype("int64")

    def _dense_ranking(self, query, ids, n):
        query_vector = _normalize(self.embedder.embed_query(query))
        params = faiss.SearchParameters(sel=faiss.IDSelectorBatch(ids))
        scores, positions = self.index.search(query_vector, min(n, len(ids)), params=params)
        return [(int(p), float(s)) for s, p in zip(scores[0], positions[0]) if p >= 0]

    def _bm25_ranking(self, query, ids, n):
        scores = self.bm25.scores(query)[ids]
        order = np.argsort(-scores, kind="stable")[:n]
        return [(int(ids[i]), float(scores[i])) for i in order if scores[i] > 0]

    def search(self, query, as_of, k=5, doc_type=None, mode="hybrid", date_aware=True):
        """Return (hits, eligible_count) for chunks published on or before as_of.

        ``mode`` is "dense" (FAISS cosine), "bm25" (lexical), or "hybrid"
        (reciprocal rank fusion of both). Every mode searches only the
        eligible ids, so late documents never compete for a slot.

        With ``date_aware``, a hybrid query that names a month ("June 2022")
        adds a third ranked list restricted to that meeting month, so short
        policy statements are not crowded out by long minutes.
        """
        if mode not in SEARCH_MODES:
            raise ValueError(f"mode must be one of {SEARCH_MODES}")
        ids = self.eligible_ids(as_of, doc_type)
        if len(ids) == 0:
            return [], 0
        if mode == "dense":
            ranked = self._dense_ranking(query, ids, k)
        elif mode == "bm25":
            ranked = self._bm25_ranking(query, ids, k)
        else:
            n = CANDIDATES_PER_RETRIEVER
            dense = [p for p, _ in self._dense_ranking(query, ids, n)]
            lexical = [p for p, _ in self._bm25_ranking(query, ids, n)]
            rankings = [dense, lexical]
            mentions = month_mentions(query) if date_aware else []
            if mentions:
                months = [f"{year}-{month:02d}" for year, month in mentions]
                dated = ids[np.isin(self._meeting_months[ids], months)]
                if len(dated):
                    rankings.append([p for p, _ in self._dense_ranking(query, dated, n)])
            ranked = reciprocal_rank_fusion(rankings)[:k]
        hits = [
            {**self.chunks[position], "score": round(score, 4)}
            for position, score in ranked[:k]
        ]
        return hits, len(ids)

_index = None


def get_index():
    global _index
    if _index is None:
        _index = FedDocIndex.load()
    return _index


def set_index(index):
    global _index
    _index = index


def build_default_index(documents_path=DOCUMENTS_PATH, out_dir=INDEX_DIR, model_name=DEFAULT_MODEL):
    index = FedDocIndex.build(load_documents(documents_path), FastEmbedEmbedder(model_name))
    index.save(out_dir)
    return index
