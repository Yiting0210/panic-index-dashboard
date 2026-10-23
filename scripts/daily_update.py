"""Daily data refresh: market data, FOMC documents, and the FOMC index.

Runs the three steps in order and prints a JSON summary. Any failure exits
non-zero, so the scheduled workflow does not commit a partial update; each
step leaves its files untouched when it fails its own checks.

Usage:
    python scripts/daily_update.py [--summary logs/daily_update.json]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_fomc  # noqa: E402
import update_market_data  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    summary = {}
    summary["market_data"] = update_market_data.run().as_dict()
    new_docs = fetch_fomc.update(2021, date.today().year, incremental=True)
    summary["fomc_documents"] = [d["doc_id"] for d in new_docs]
    if new_docs:
        from rag import update_default_index

        _, added = update_default_index()
        summary["fomc_index_chunks_added"] = added
    else:
        summary["fomc_index_chunks_added"] = 0

    text = json.dumps(summary, indent=2)
    print(text)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
