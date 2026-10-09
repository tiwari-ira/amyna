"""Tiny pure-Python BM25 index over policy chunks (no external services)."""

import math
import re
from collections import Counter
from collections.abc import Callable

from .ingest import PolicyChunk

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "for", "from", "has", "have",
        "in", "is", "it", "its", "may", "must", "not", "of", "on", "or", "that", "the", "this",
        "to", "was", "were", "will", "with", "without",
    }
)  # fmt: skip


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
    return [t[:-1] if len(t) > 4 and t.endswith("s") else t for t in tokens if t not in STOPWORDS]


class BM25Index:
    def __init__(self, chunks: list[PolicyChunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self._k1, self._b = k1, b
        self._docs = [
            Counter(tokenize(f"{c.document.title} {c.section or ''} {c.text}")) for c in chunks
        ]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._avg = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        frequency: Counter[str] = Counter()
        for doc in self._docs:
            frequency.update(doc.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in frequency.items()}

    def _score(self, index: int, terms: list[str]) -> float:
        doc, length = self._docs[index], self._lengths[index]
        score = 0.0
        for term in terms:
            tf = doc.get(term, 0)
            if tf:
                norm = tf + self._k1 * (1 - self._b + self._b * length / (self._avg or 1))
                score += self._idf.get(term, 0.0) * tf * (self._k1 + 1) / norm
        return score

    def search(
        self, query: str, *, k: int = 5, predicate: Callable[[PolicyChunk], bool] | None = None
    ) -> list[tuple[PolicyChunk, float]]:
        terms = tokenize(query)
        scored = [
            (chunk, self._score(i, terms))
            for i, chunk in enumerate(self.chunks)
            if predicate is None or predicate(chunk)
        ]
        ranked = sorted((pair for pair in scored if pair[1] > 0), key=lambda p: -p[1])
        return ranked[:k]
