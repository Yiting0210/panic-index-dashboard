"""Okapi BM25 over chunk text, used alongside dense retrieval.

FOMC questions often hinge on exact tokens (names such as "Hammack",
amounts such as "$25 billion", ranges such as "5-1/4") that small embedding
models blur together. BM25 recovers those matches.
"""
import math
import re
from collections import Counter

import numpy as np

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how in is it its "
    "of on or that the their this to was were what when which who why will with".split()
)


def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.doc_tokens = [Counter(tokenize(text)) for text in texts]
        self.doc_len = np.array([sum(c.values()) for c in self.doc_tokens], dtype="float64")
        self.avg_len = float(self.doc_len.mean()) if len(self.doc_len) else 0.0
        document_frequency = Counter()
        for counts in self.doc_tokens:
            document_frequency.update(counts.keys())
        n = len(self.doc_tokens)
        self.idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }
        self.postings = {}
        for doc_id, counts in enumerate(self.doc_tokens):
            for term, tf in counts.items():
                self.postings.setdefault(term, []).append((doc_id, tf))

    def scores(self, query):
        scores = np.zeros(len(self.doc_tokens))
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / max(self.avg_len, 1e-9))
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc_id, tf in self.postings[term]:
                scores[doc_id] += idf * tf * (self.k1 + 1) / (tf + norm[doc_id])
        return scores


def reciprocal_rank_fusion(rankings, k=60):
    """Fuse ranked id lists; returns ids ordered by fused score with scores."""
    fused = Counter()
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            fused[item] += 1.0 / (k + rank + 1)
    return fused.most_common()
