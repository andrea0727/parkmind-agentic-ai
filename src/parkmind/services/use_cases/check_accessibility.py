"""check_accessibility -- match a guest's derived flags against the park's
published safety notices (section 30, section 12 [C15], section 20 rule 10;
backlog P0-26a).

This is matching, not medicine (section 12 "What this is not"): both sides are
closed ``RideRestriction`` sets and the check is their intersection. A notice
lists the taxonomy items that conflict at that attraction; a guest's flags are
their ``ride_restrictions`` plus one item derived from mobility:

* ``WHEELCHAIR``, ``ECV`` or ``STROLLER_AS_WHEELCHAIR`` adds
  ``REQUIRES_TRANSFER_FROM_WHEELCHAIR``. The contracts do not say whether the
  guest can transfer, so a transfer-required attraction fails closed for them
  (decided 2026-09-28). ECV is treated like a wheelchair: the taxonomy has no
  separate "transfer from ECV to wheelchair" item.
* ``LIMITED_WALKING`` never conflicts with a notice; walking limits are rule 9.

The other ``AccessibilityRequirements`` fields never cross this path either:
``daily_walking_limit_minutes``, ``heat_sensitivity`` and ``rest_frequency_minutes``
are enforced by the ConstraintChecker's rule 9 (walking, weather, rest cadence),
not by matching a notice.

Fail closed (section 30, section 43): when a guest with any flag meets an
attraction with no notice on file, the result is *not eligible*. A guest with
no flags at all is eligible everywhere -- section 30 excludes an uncovered
attraction "for any guest with restrictions", not for every guest.

C19: this path reads ``AccessibilityRequirements`` and returns only the
derived ``AccessibilityCheck``. It logs nothing and touches no repository, so
no requirement value can reach a table, a log or a trace from here.

Coverage (section 30 [C14], section 45): because an uncovered attraction is
excluded for every guest with restrictions, corpus coverage is a product
metric. ``notice_coverage`` reports it over the curated attraction catalog.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from parkmind.core.contracts import (
    AccessibilityCheck,
    AccessibilityRequirements,
    MobilityRequirement,
    RideRestriction,
)
from parkmind.services.ports import KnowledgeStore

NOTICE_SOURCE = "park_safety_notice"

_NEEDS_WHEELCHAIR_ACCESS = frozenset(
    {
        MobilityRequirement.WHEELCHAIR,
        MobilityRequirement.ECV,
        MobilityRequirement.STROLLER_AS_WHEELCHAIR,
    }
)


def guest_flags(requirements: AccessibilityRequirements) -> frozenset[RideRestriction]:
    """The notice items that conflict with this guest."""
    flags = set(requirements.ride_restrictions)
    if _NEEDS_WHEELCHAIR_ACCESS.intersection(requirements.mobility_requirements):
        flags.add(RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR)
    return frozenset(flags)


def check_accessibility(
    requirements: AccessibilityRequirements,
    attraction_id: str,
    store: KnowledgeStore,
) -> AccessibilityCheck:
    """Whether the guest may be served at ``attraction_id``, and the first conflict.

    ``conflicting_requirement`` is the first conflicting item in
    ``RideRestriction`` declaration order, so the same inputs always name the
    same conflict. It is ``None`` when the guest is eligible, and also when the
    guest is ineligible only because no notice is on file
    (``provenance["notice_on_file"]`` is then ``False``).
    """
    flags = guest_flags(requirements)
    notice = store.notice_for(attraction_id)
    provenance = {
        "source": NOTICE_SOURCE,
        "version": store.corpus_version,
        "notice_on_file": notice is not None,
    }

    conflict: str | None = None
    if not flags:
        eligible = True
    elif notice is None:
        eligible = False
    else:
        conflicts = [
            item for item in RideRestriction if item in flags and item in notice
        ]
        eligible = not conflicts
        conflict = conflicts[0].value if conflicts else None

    return AccessibilityCheck(
        attraction_id=attraction_id,
        guest_id=requirements.guest_id,
        eligible=eligible,
        conflicting_requirement=conflict,
        provenance=provenance,
    )


@dataclass(frozen=True)
class NoticeCoverage:
    """How much of the attraction catalog the notice corpus covers (section 45).

    ``missing`` attractions fail closed for every guest with restrictions;
    ``not_in_catalog`` are corpus entries for ids the catalog no longer has
    (stale entries to review). All id tuples are sorted.
    """

    corpus_version: str
    covered: tuple[str, ...]
    missing: tuple[str, ...]
    not_in_catalog: tuple[str, ...]

    @property
    def ratio(self) -> float:
        """Covered share of the catalog; 1.0 for an empty catalog (nothing to cover)."""
        total = len(self.covered) + len(self.missing)
        return len(self.covered) / total if total else 1.0


def notice_coverage(
    store: KnowledgeStore, catalog_ids: Iterable[str]
) -> NoticeCoverage:
    """Which catalog attractions have a notice on file."""
    catalog = frozenset(catalog_ids)
    on_file = store.covered_attraction_ids()
    return NoticeCoverage(
        corpus_version=store.corpus_version,
        covered=tuple(sorted(catalog & on_file)),
        missing=tuple(sorted(catalog - on_file)),
        not_in_catalog=tuple(sorted(on_file - catalog)),
    )
