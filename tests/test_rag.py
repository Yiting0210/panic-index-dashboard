"""RAG chunking, BM25, and as-of filtering with a deterministic fake embedder."""
import hashlib
import os
import sys
from datetime import date

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from rag import FedDocIndex, chunk_documents, split_text
from rag.lexical import BM25, reciprocal_rank_fusion
from rag.query import month_mentions


class HashEmbedder:
    """Bag-of-words hashed into 64 dims: deterministic and dependency-free."""

    model_name = "test-hash-embedder"

    def _embed(self, text):
        vector = np.zeros(64, dtype="float32")
        for token in text.lower().split():
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % 64] += 1
        return vector

    def embed_documents(self, texts):
        return np.stack([self._embed(t) for t in texts])

    def embed_query(self, text):
        return self._embed(text)[None, :]


def doc(doc_id, doc_type, meeting, published, text):
    return {
        "doc_id": doc_id, "doc_type": doc_type, "title": doc_id,
        "meeting_date": meeting, "published_at": published,
        "url": f"https://example.test/{doc_id}", "text": text,
    }


DOCS = [
    doc("statement-20220316", "statement", "2022-03-16", "2022-03-16",
        "The Committee decided to raise the target range for the federal funds rate."),
    doc("minutes-20220316", "minutes", "2022-03-16", "2022-04-06",
        "Participants agreed monthly caps of $60 billion for Treasury securities "
        "and $35 billion for agency MBS would be appropriate for balance sheet runoff."),
    doc("statement-20220504", "statement", "2022-05-04", "2022-05-04",
        "The Committee decided to raise the target range and begin reducing "
        "holdings of Treasury securities on June 1 balance sheet runoff."),
] + [
    doc(f"minutes-2023{m:02d}01", "minutes", f"2023-{m:02d}-01", f"2023-{m:02d}-21",
        "balance sheet runoff caps balance sheet runoff caps " * 3)
    for m in range(1, 13)
]


@pytest.fixture(scope="module")
def index():
    return FedDocIndex.build(DOCS, HashEmbedder())


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
def test_search_never_returns_documents_after_as_of(index, mode):
    for as_of in [date(2022, 3, 16), date(2022, 4, 5), date(2022, 4, 6), date(2023, 6, 30)]:
        hits, _ = index.search("balance sheet runoff caps", as_of=as_of, k=10, mode=mode)
        assert all(date.fromisoformat(h["published_at"]) <= as_of for h in hits)


def test_minutes_are_dated_by_release_not_meeting(index):
    hits, _ = index.search("monthly caps for Treasury runoff", as_of=date(2022, 4, 5), k=10)
    assert "minutes-20220316" not in {h["doc_id"] for h in hits}
    hits, _ = index.search("monthly caps for Treasury runoff", as_of=date(2022, 4, 6), k=10)
    assert "minutes-20220316" in {h["doc_id"] for h in hits}


def test_prefiltering_fills_k_even_when_newer_documents_dominate(index):
    # The 2023 minutes match this query best, but none are eligible yet.
    hits, eligible = index.search("balance sheet runoff caps", as_of=date(2022, 5, 10),
                                  k=3, mode="dense")
    assert eligible == 3
    assert len(hits) == 3


def test_no_eligible_documents_returns_empty(index):
    assert index.search("rates", as_of=date(2020, 1, 1)) == ([], 0)


def test_doc_type_filter(index):
    hits, _ = index.search("raise the target range", as_of=date(2023, 12, 31),
                           k=10, doc_type="statement")
    assert hits and all(h["doc_type"] == "statement" for h in hits)


def test_save_and_load_round_trip(index, tmp_path):
    index.save(tmp_path)
    loaded = FedDocIndex.load(tmp_path, embedder=HashEmbedder())
    query = ("raise the target range", date(2023, 12, 31))
    assert loaded.search(*query) == index.search(*query)


def test_load_rejects_mismatched_embedder(index, tmp_path):
    index.save(tmp_path)
    other = HashEmbedder()
    other.model_name = "different-model"
    with pytest.raises(ValueError, match="built with"):
        FedDocIndex.load(tmp_path, embedder=other)


def test_split_text_respects_max_chars_and_keeps_text():
    paragraphs = [("word " * 60).strip() for _ in range(10)]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text, max_chars=700)
    assert all(len(c) <= 700 for c in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_bm25_prefers_exact_rare_tokens():
    bm25 = BM25(["Hammack preferred to hold rates", "rates were held steady", "rates rose"])
    assert int(np.argmax(bm25.scores("Why did Hammack dissent?"))) == 0


def test_reciprocal_rank_fusion_rewards_agreement():
    fused = reciprocal_rank_fusion([[1, 2, 3], [2, 1, 4]])
    assert {fused[0][0], fused[1][0]} == {1, 2}
    assert fused[-1][0] in {3, 4}


def test_month_mentions():
    assert month_mentions("What happened in June 2022 and September 18, 2024?") == [
        (2022, 6), (2024, 9)
    ]
    assert month_mentions("What did the Fed say about May?") == []


def test_incremental_add_matches_full_build():
    early, late = DOCS[:3], DOCS[3:]
    incremental = FedDocIndex.build(early, HashEmbedder())
    assert incremental.add_documents(DOCS) == len(list(chunk_documents(late)))
    assert incremental.add_documents(DOCS) == 0  # already indexed
    full = FedDocIndex.build(DOCS, HashEmbedder())
    for mode in ("dense", "bm25", "hybrid"):
        query = ("balance sheet runoff caps in March 2023", date(2023, 12, 31))
        assert incremental.search(*query, mode=mode) == full.search(*query, mode=mode)
