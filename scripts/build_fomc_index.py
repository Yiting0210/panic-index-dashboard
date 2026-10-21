"""Chunk data/fomc/documents.jsonl, embed it, and write the FAISS index.

Usage:
    python scripts/build_fomc_index.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag import INDEX_DIR, build_default_index  # noqa: E402


def main():
    started = time.perf_counter()
    index = build_default_index()
    documents = len({c["doc_id"] for c in index.chunks})
    print(
        f"Indexed {len(index.chunks)} chunks from {documents} documents "
        f"with {index.model_name} in {time.perf_counter() - started:.1f}s -> {INDEX_DIR}"
    )


if __name__ == "__main__":
    main()
