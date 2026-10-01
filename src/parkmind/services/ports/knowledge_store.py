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
"""

from typing import Protocol

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
