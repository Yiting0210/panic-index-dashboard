"""Chunk data/fomc/documents.jsonl, embed it, and write the FAISS index.

Usage:
    python scripts/build_fomc_index.py                # full rebuild
    python scripts/build_fomc_index.py --incremental  # embed new documents only
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag import INDEX_DIR, build_default_index, update_default_index  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incremental", action="store_true",
                        help="Append documents missing from the saved index")
    args = parser.parse_args()

    started = time.perf_counter()
    if args.incremental:
        index, added = update_default_index()
        print(f"Added {added} chunks; index now has {len(index.chunks)} chunks "
              f"({time.perf_counter() - started:.1f}s)")
        return
    index = build_default_index()
    documents = len({c["doc_id"] for c in index.chunks})
    print(
        f"Indexed {len(index.chunks)} chunks from {documents} documents "
        f"with {index.model_name} in {time.perf_counter() - started:.1f}s -> {INDEX_DIR}"
    )


if __name__ == "__main__":
    main()
