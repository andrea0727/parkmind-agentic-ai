"""Guest-spoken attraction names -> catalog ``node_id``s.

``PartyConstraints.must_do`` and ``avoid`` are ``node_id`` sets: the optimizer
and the ConstraintChecker compare them to ``Attraction.node_id`` and nothing
else. A raw name there is never enforced (an avoided ride would be scheduled
without a violation), so every name is resolved against the park's catalog
before it becomes a hard constraint.

Fail closed, never guess: a name that matches no attraction, or more than one,
is reported back so the human can say which one they mean. Matching is by
normalized name (lowercase, no punctuation or possessive 's): first an exact
match, then a unique match where every word the guest said is a word of the
attraction's name ("splash" -> "Splash Mountain").
The human sees the resolved official name when they confirm.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from parkmind.core.contracts import Attraction
from parkmind.services.ports import AttractionRepository


class CatalogUnavailableError(RuntimeError):
    """The park has no catalog to resolve names against (an infrastructure problem)."""


@dataclass(frozen=True)
class ResolvedAttraction:
    node_id: str
    name: str


@dataclass(frozen=True)
class NameResolution:
    resolved: Mapping[str, ResolvedAttraction] = field(default_factory=dict)
    """Spoken name -> the attraction it unambiguously refers to."""
    unknown: tuple[str, ...] = ()
    ambiguous: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """Spoken name -> official names of the attractions it could mean."""

    @property
    def complete(self) -> bool:
        return not self.unknown and not self.ambiguous

    def node_ids(self, names: Iterable[str]) -> list[str]:
        """The distinct node ids of ``names``, in order. Every name must be resolved."""
        return list(dict.fromkeys(self.resolved[name].node_id for name in names))


_POSSESSIVE = re.compile(r"['’`]s\b")
_PUNCTUATION = re.compile(r"[^\w\s]")


def _normalize(text: str) -> str:
    """Case, possessive 's and punctuation do not tell attractions apart.

    "Peter Pan" -> "Peter Pan's Flight" and "small world" -> '"it's a small
    world"' only match once both sides are normalized the same way.
    """
    without_possessive = _POSSESSIVE.sub("", text.lower())
    return " ".join(_PUNCTUATION.sub(" ", without_possessive).split())


class AttractionNameResolver:
    def __init__(self, repository: AttractionRepository, park_id: str) -> None:
        self._repository = repository
        self._park_id = park_id

    def resolve(self, names: Iterable[str]) -> NameResolution:
        distinct = list(dict.fromkeys(names))
        if not distinct:
            return NameResolution()
        catalog = self._repository.list_attractions(self._park_id)
        if not catalog:
            raise CatalogUnavailableError(f"no attraction catalog for park {self._park_id!r}")

        resolved: dict[str, ResolvedAttraction] = {}
        unknown: list[str] = []
        ambiguous: dict[str, tuple[str, ...]] = {}
        for name in distinct:
            matches = _match(name, catalog)
            if len(matches) == 1:
                resolved[name] = ResolvedAttraction(matches[0].node_id, matches[0].name)
            elif matches:
                ambiguous[name] = tuple(sorted(a.name for a in matches))
            else:
                unknown.append(name)
        return NameResolution(resolved, tuple(unknown), ambiguous)


def _match(name: str, catalog: list[Attraction]) -> list[Attraction]:
    wanted = _normalize(name)
    if not wanted:
        return []
    exact = [a for a in catalog if _normalize(a.name) == wanted]
    if exact:
        return exact
    words = set(wanted.split())
    return [a for a in catalog if words <= set(_normalize(a.name).split())]
