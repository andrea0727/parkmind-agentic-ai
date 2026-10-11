"""KnowledgeStore -- the park's published safety notices (section 30 [C21], P0-26a).

``check_accessibility`` (services/use_cases/check_accessibility.py) matches a
guest's derived flags against what this port returns. Two adapters sit behind
it in the baseline: in-memory (services/clients/knowledge/in_memory.py) and
pgvector (P0-26). Semantic search (``search_policies``,
``find_similar_attractions``) is added to this port by P0-26.

A notice is the set of ``RideRestriction`` items the park publishes for an
attraction -- the taxonomy is one-to-one with the park's safety notices
(section 12 [C15]). ``USES_SERVICE_ANIMAL`` in a notice means service animals
may not ride (section 33: "the attraction side lives in the notice corpus").
Only closed enum members cross this port: never the notice text, never a
free-text requirement.

``KnowledgeSearch`` (P0-26) is the semantic half of the same capability: ranked
passages of the reviewed knowledge corpus (park policies, accessibility
information, attraction profiles) for ``search_policies`` and
``find_similar_attractions``. It is a separate protocol so a notice-only store
stays valid, and so ``check_accessibility`` never depends on retrieval (section
43: accessibility checks do not degrade).
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from parkmind.core.contracts import RideRestriction


class KnowledgeStore(Protocol):
    @property
    def corpus_version(self) -> str:
        """Version of the reviewed notice corpus; goes into every check's provenance."""
        ...

    def notice_for(self, attraction_id: str) -> frozenset[RideRestriction] | None:
        """The published notice for ``attraction_id``, or ``None`` if none is on file.

        An empty set is a notice that restricts nothing; ``None`` means the
        attraction is not covered, and ``check_accessibility`` fails closed.
        """
        ...

    def covered_attraction_ids(self) -> frozenset[str]:
        """Every attraction id with a notice on file (for the coverage report, section 45)."""
        ...


KnowledgeKind = Literal["policy", "faq", "accessibility", "attraction_profile"]


@dataclass(frozen=True)
class KnowledgeChunk:
    """One quoted passage of the reviewed corpus, with where it was read and when."""

    chunk_id: str
    kind: KnowledgeKind
    title: str
    body: str
    source_url: str
    reviewed_on: date
    attraction_id: str | None = None
    """Set for passages about one attraction (always for ``attraction_profile``)."""


@dataclass(frozen=True)
class KnowledgeHit:
    chunk: KnowledgeChunk
    score: float
    """Higher is closer: cosine similarity (semantic) or a normalized keyword score."""


class KnowledgeSearch(Protocol):
    @property
    def corpus_version(self) -> str:
        """Version of the knowledge corpus the answers come from."""
        ...

    @property
    def strategy(self) -> str:
        """How passages are ranked, e.g. ``semantic:<model>`` or ``keyword``."""
        ...

    def search_policies(self, query: str, k: int) -> list[KnowledgeHit]:
        """The ``k`` passages (policy, faq, accessibility) closest to ``query``.

        Raises ``KnowledgeUnavailableError`` when it cannot rank them.
        """
        ...

    def similar_attractions(
        self, *, attraction_id: str | None, text: str | None, k: int
    ) -> list[KnowledgeHit]:
        """Attraction profiles closest to an attraction's own profile, or to ``text``.

        Raises ``KnowledgeUnavailableError`` when it cannot rank them.
        """
        ...
