"""Split FOMC documents into retrieval chunks that keep their release date."""
import json
import re
from pathlib import Path

MAX_CHARS = 1200
MIN_CHARS = 200


def _split_long_paragraph(paragraph, max_chars):
    sentences = re.split(r"(?<=[.;])\s+", paragraph)
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def split_text(text, max_chars=MAX_CHARS, min_chars=MIN_CHARS):
    """Greedy paragraph packing; long paragraphs are split on sentence ends."""
    paragraphs = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            paragraphs.extend(_split_long_paragraph(paragraph, max_chars))
        else:
            paragraphs.append(paragraph)

    chunks, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        if chunks and len(current) < min_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{current}"
        else:
            chunks.append(current)
    return chunks


def chunk_documents(documents, max_chars=MAX_CHARS):
    """Yield chunk records that inherit every document's metadata."""
    for doc in documents:
        for i, text in enumerate(split_text(doc["text"], max_chars=max_chars)):
            yield {
                "chunk_id": f"{doc['doc_id']}#{i:03d}",
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "title": doc["title"],
                "meeting_date": doc["meeting_date"],
                "published_at": doc["published_at"],
                "url": doc["url"],
                "text": text,
            }


def embedding_text(chunk):
    """Text sent to the embedding model; the title anchors dated queries."""
    return f"{chunk['title']} (released {chunk['published_at']})\n{chunk['text']}"


def load_documents(path):
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
