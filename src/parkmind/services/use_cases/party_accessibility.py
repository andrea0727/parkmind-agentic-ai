"""Per-party accessibility results, derived on demand and kept out of graph state [C19].

``AccessibilityCheck`` results are derived from a guest's stored requirements
(who can ride what), so the checkpointed ``LiveContext`` carries none for the
party. Every use case that needs them re-derives them from the SessionStore.
"""

from collections.abc import Collection, Sequence

from parkmind.core.contracts import (
    AccessibilityCheck,
    AccessibilityRequirements,
    Attraction,
    LiveContext,
)
from parkmind.services.ports import KnowledgeStore
from parkmind.services.use_cases.check_accessibility import check_accessibility


def party_checks(
    requirements: Sequence[AccessibilityRequirements],
    catalog: Sequence[Attraction],
    knowledge: KnowledgeStore,
) -> list[AccessibilityCheck]:
    return [
        check_accessibility(req, attraction.node_id, knowledge)
        for req in requirements
        for attraction in catalog
    ]


def with_checks(live_context: LiveContext, checks: Sequence[AccessibilityCheck]) -> LiveContext:
    """``checks`` replace any earlier result of the same guests."""
    checked = {c.guest_id for c in checks}
    kept = [r for r in live_context.accessibility_results if r.guest_id not in checked]
    return live_context.model_copy(update={"accessibility_results": [*kept, *checks]})


def without_guests(live_context: LiveContext, guest_ids: Collection[str]) -> LiveContext:
    """The context as it may be checkpointed: no result for any guest in ``guest_ids``."""
    kept = [r for r in live_context.accessibility_results if r.guest_id not in guest_ids]
    return live_context.model_copy(update={"accessibility_results": kept})
