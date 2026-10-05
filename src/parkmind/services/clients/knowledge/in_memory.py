"""In-memory KnowledgeStore: a versioned map of attraction id -> published notice.

Flags are validated at construction, so a notice can only ever hold closed
``RideRestriction`` members. A free-text requirement ("no heart problems")
cannot reach ``check_accessibility``: it fails here, loudly, instead of
silently never matching.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from parkmind.core.contracts import RideRestriction


class InMemoryKnowledgeStore:
    def __init__(
        self,
        notices: Mapping[str, Iterable[RideRestriction]],
        *,
        corpus_version: str,
    ) -> None:
        if not corpus_version:
            raise ValueError("a knowledge corpus needs a version")
        validated: dict[str, frozenset[RideRestriction]] = {}
        for attraction_id, flags in notices.items():
            flag_set = frozenset(flags)
            free_text = sorted(
                repr(f) for f in flag_set if not isinstance(f, RideRestriction)
            )
            if free_text:
                raise TypeError(
                    f"notice for {attraction_id!r} holds values that are not "
                    f"RideRestriction members: {', '.join(free_text)}"
                )
            validated[attraction_id] = flag_set
        # Read-only view: a loaded corpus version never changes under its callers.
        self._notices: Mapping[str, frozenset[RideRestriction]] = MappingProxyType(
            validated
        )
        self._corpus_version = corpus_version

    @property
    def corpus_version(self) -> str:
        return self._corpus_version

    def notice_for(self, attraction_id: str) -> frozenset[RideRestriction] | None:
        return self._notices.get(attraction_id)

    def covered_attraction_ids(self) -> frozenset[str]:
        return frozenset(self._notices)
