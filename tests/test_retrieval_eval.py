"""Retrieval quality and leakage on the real FOMC index (needs the embedding model)."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("fastembed")

from evals.retrieval_eval import evaluate, load_questions  # noqa: E402
from rag import get_index  # noqa: E402

RECALL_FLOOR = 0.85


@pytest.fixture(scope="module")
def index():
    try:
        return get_index()
    except Exception as exc:  # model download unavailable offline
        pytest.skip(f"FOMC index unavailable: {exc}")


def test_hybrid_retrieval_meets_recall_floor_without_leakage(index):
    summary, _ = evaluate(index, load_questions(), config="hybrid", k=5)
    assert summary["leakage_rate"] == 0
    assert summary["recall@5"] >= RECALL_FLOOR


@pytest.mark.parametrize("as_of", [date(2021, 2, 1), date(2022, 3, 17), date(2024, 9, 18),
                                   date(2026, 1, 1)])
def test_leakage_sweep_across_queries(index, as_of):
    queries = ["rate increase", "balance sheet runoff", "dissent", "banking system",
               "inflation remains elevated", "minutes of the meeting"]
    for query in queries:
        for mode in ("dense", "bm25", "hybrid"):
            hits, _ = index.search(query, as_of=as_of, k=10, mode=mode)
            assert all(date.fromisoformat(h["published_at"]) <= as_of for h in hits)
