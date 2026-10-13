"""Keyword search over the knowledge corpus: the section 43 fallback for retrieval.

When semantic search cannot answer (no pgvector index for this corpus and
model, the model is not in the cache, the database failed), the same reviewed
passages are ranked by BM25 over accent-folded words of title and body. It
needs nothing but the corpus file, so it always answers -- lexically: an
English question matches Spanish passages only where they share words (names,
numbers). Results say so in their ``strategy``.
"""

import math
from collections import Counter
from collections.abc import Callable, Sequence

from parkmind.services.ports import KnowledgeChunk, KnowledgeHit

from .embeddings import words
from .knowledge_corpus import KNOWLEDGE_CORPUS_VERSION, knowledge_corpus

_STOPWORDS = frozenset(
    [
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "es",
        "esta",
        "este",
        "la",
        "las",
        "lo",
        "los",
        "para",
        "por",
        "que",
        "se",
        "su",
        "sus",
        "un",
        "una",
        "y",
        "the",
        "of",
        "and",
        "to",
        "in",
        "for",
        "on",
        "is",
        "are",
        "with",
        "by",
        "at",
        "as",
        "be",
        "can",
        "my",
        "our",
        "you",
        "your",
        "this",
        "that",
        "it",
    ]
)
_K1 = 1.5
_B = 0.75


def _terms(text: str) -> list[str]:
    return [w for w in words(text) if w not in _STOPWORDS and len(w) > 1]


class KeywordKnowledgeSearch:
    def __init__(
        self,
        chunks: Sequence[KnowledgeChunk],
        *,
        corpus_version: str = KNOWLEDGE_CORPUS_VERSION,
    ) -> None:
        self._chunks = list(chunks)
        self._corpus_version = corpus_version
        self._terms = [Counter(_terms(f"{c.title} {c.body}")) for c in self._chunks]
        self._lengths = [sum(t.values()) for t in self._terms]
        self._avg_length = (
            (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        )
        document_frequency: Counter[str] = Counter()
        for terms in self._terms:
            document_frequency.update(terms.keys())
        n = len(self._chunks)
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    @property
    def corpus_version(self) -> str:
        return self._corpus_version

    @property
    def strategy(self) -> str:
        return "keyword"

    def search_policies(self, query: str, k: int) -> list[KnowledgeHit]:
        return self._rank(_terms(query), k, lambda c: c.kind != "attraction_profile")

    def similar_attractions(
        self, *, attraction_id: str | None, text: str | None, k: int
    ) -> list[KnowledgeHit]:
        if attraction_id is not None:
            profile = next(
                (
                    c
                    for c in self._chunks
                    if c.kind == "attraction_profile"
                    and c.attraction_id == attraction_id
                ),
                None,
            )
            if profile is None:
                return []
            query = _terms(f"{profile.title} {profile.body}")
        else:
            query = _terms(text or "")
        return self._rank(
            query,
            k,
            lambda c: (
                c.kind == "attraction_profile" and c.attraction_id != attraction_id
            ),
        )

    def _rank(
        self, query: list[str], k: int, keep: Callable[[KnowledgeChunk], bool]
    ) -> list[KnowledgeHit]:
        scored: list[tuple[float, int]] = []
        for index, chunk in enumerate(self._chunks):
            if not keep(chunk):
                continue
            score = self._bm25(query, index)
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda pair: (-pair[0], self._chunks[pair[1]].chunk_id))
        top = scored[:k]
        best = top[0][0] if top else 1.0
        return [KnowledgeHit(self._chunks[i], round(s / best, 6)) for s, i in top]

    def _bm25(self, query: list[str], index: int) -> float:
        terms, length = self._terms[index], self._lengths[index]
        score = 0.0
        for term in set(query):
            tf = terms.get(term, 0)
            if tf:
                norm = (
                    tf
                    * (_K1 + 1)
                    / (tf + _K1 * (1 - _B + _B * length / (self._avg_length or 1)))
                )
                score += self._idf.get(term, 0.0) * norm
        return score


def keyword_knowledge_search() -> KeywordKnowledgeSearch:
    """Keyword search over the current reviewed corpus."""
    return KeywordKnowledgeSearch(knowledge_corpus())
