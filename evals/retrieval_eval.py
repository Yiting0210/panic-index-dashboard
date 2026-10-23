"""Retrieval evaluation for search_fed_docs: recall@k, MRR, and leakage.

Usage:
    python -m evals.retrieval_eval            # ablation over all configs
    python -m evals.retrieval_eval --config hybrid --k 5
"""
import argparse
import json
from datetime import date
from pathlib import Path
from statistics import mean

import yaml

from evals.metrics import leaked_hits, recall_at_k, reciprocal_rank, unique_doc_ids

DATASET = Path(__file__).parent / "datasets" / "retrieval.yaml"
RESULTS_DIR = Path(__file__).parent / "results"


def load_questions(path=DATASET):
    questions = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]
    for q in questions:
        if isinstance(q["as_of"], str):
            q["as_of"] = date.fromisoformat(q["as_of"])
    return questions


# label -> search() keyword arguments, in ablation order.
CONFIGS = {
    "dense": {"mode": "dense"},
    "bm25": {"mode": "bm25"},
    "hybrid_no_date": {"mode": "hybrid", "date_aware": False},
    "hybrid": {"mode": "hybrid", "date_aware": True},
}


def evaluate(index, questions, config="hybrid", k=5):
    rows = []
    for q in questions:
        hits, eligible = index.search(q["question"], as_of=q["as_of"], k=k, **CONFIGS[config])
        doc_ids = unique_doc_ids(hits)
        rows.append({
            "id": q["id"],
            "recall": recall_at_k(doc_ids, q["relevant"], k),
            "reciprocal_rank": reciprocal_rank(doc_ids, q["relevant"]),
            "leaked": len(leaked_hits(hits, q["as_of"])),
            "eligible_chunks": eligible,
            "retrieved": doc_ids,
            "relevant": q["relevant"],
        })
    summary = {
        "config": config,
        "k": k,
        "questions": len(rows),
        f"recall@{k}": round(mean(r["recall"] for r in rows), 4),
        "mrr": round(mean(r["reciprocal_rank"] for r in rows), 4),
        "leakage_rate": round(mean(r["leaked"] > 0 for r in rows), 4),
    }
    return summary, rows


def main():
    from rag import get_index

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", choices=["all", *CONFIGS], default="all")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    index = get_index()
    questions = load_questions()
    configs = list(CONFIGS) if args.config == "all" else [args.config]
    report = {}
    for config in configs:
        summary, rows = evaluate(index, questions, config=config, k=args.k)
        report[config] = {"summary": summary, "misses": [r for r in rows if r["recall"] < 1]}
        print(json.dumps(summary))
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / "retrieval.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
