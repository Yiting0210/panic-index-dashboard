"""Split FOMC documents into retrieval chunks that keep their release date."""
import json
import re
from pathlib import Path

MAX_CHARS = 1200
MIN_CHARS = 200
# Leading part of a reporter's question repeated on each piece of a long answer.
QUESTION_CONTEXT_CHARS = 400


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


def _transcript_exchanges(text):
    """Group transcript turns into the Chair's opening statement and Q&A pairs.

    Turns start with the speaker's name ("CHAIR POWELL. ..."). Consecutive
    non-Chair turns (moderator plus reporter) form one question, and the
    Chair turns that follow form its answer.
    """
    opening, exchanges = [], []
    for turn in (t.strip() for t in text.split("\n\n")):
        if not turn:
            continue
        if turn.startswith("CHAIR"):
            (exchanges[-1][1] if exchanges else opening).append(turn)
        elif exchanges and not exchanges[-1][1]:
            exchanges[-1][0].append(turn)
        else:
            exchanges.append(([turn], []))
    return opening, exchanges


def _question_context(question, limit=QUESTION_CONTEXT_CHARS):
    if len(question) <= limit:
        return question
    return question[:limit].rsplit(" ", 1)[0] + " ..."


def split_transcript(text, max_chars=MAX_CHARS, min_chars=MIN_CHARS):
    """Chunk a press conference so every piece of an answer keeps its question."""
    opening, exchanges = _transcript_exchanges(text)
    chunks = split_text("\n\n".join(opening), max_chars, min_chars) if opening else []
    for question_turns, answer_turns in exchanges:
        question = "\n\n".join(question_turns)
        answer = "\n\n".join(answer_turns)
        whole = f"{question}\n\n{answer}".strip()
        if len(whole) < min_chars and chunks:
            # e.g. the closing "Thank you all." exchange.
            chunks[-1] = f"{chunks[-1]}\n\n{whole}"
        elif len(whole) <= max_chars or not answer:
            chunks.extend(split_text(whole, max_chars, min_chars))
        else:
            context = _question_context(question)
            budget = max(max_chars - len(context) - 2, min_chars)
            chunks.extend(f"{context}\n\n{piece}"
                          for piece in split_text(answer, budget, min_chars))
    return chunks


def chunk_documents(documents, max_chars=MAX_CHARS):
    """Yield chunk records that inherit every document's metadata."""
    for doc in documents:
        splitter = split_transcript if doc["doc_type"] == "press_conference" else split_text
        for i, text in enumerate(splitter(doc["text"], max_chars=max_chars)):
            chunk = {
                "chunk_id": f"{doc['doc_id']}#{i:03d}",
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "title": doc["title"],
                "meeting_date": doc["meeting_date"],
                "published_at": doc["published_at"],
                "url": doc["url"],
                "text": text,
            }
            if doc.get("speaker"):
                chunk["speaker"] = doc["speaker"]
            yield chunk


def embedding_text(chunk):
    """Text sent to the embedding model; the title anchors dated queries."""
    return f"{chunk['title']} (released {chunk['published_at']})\n{chunk['text']}"


def load_documents(path):
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
