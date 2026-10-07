"""Use case: thin wrapper around PreferenceScorer (P0-17).

Keeps services.personalization (pure deterministic core, no I/O) decoupled
from the agents/graph layer: graph nodes and tools reach the scorer only
through this use case (see .importlinter). The caller passes the
``KnowledgeStore`` adapter (in-memory or pgvector) and, for walking comfort,
the ``ParkGraph`` and the party's current location.
"""

from collections.abc import Sequence

from parkmind.core.contracts import (
    Attraction,
    GroupObjective,
    GuestProfile,
    LiveContext,
    Plan,
)
from parkmind.services.personalization.preference_scorer import (
    PreferenceScorer,
    PreferenceScores,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import KnowledgeStore


class ScorePreferencesUseCase:
    def __init__(self, preference_scorer: PreferenceScorer | None = None) -> None:
        self.preference_scorer = preference_scorer or PreferenceScorer()

    def execute(
        self,
        *,
        objective: GroupObjective,
        profiles: Sequence[GuestProfile],
        attractions: Sequence[Attraction],
        live_context: LiveContext,
        knowledge: KnowledgeStore,
        park_graph: ParkGraph | None = None,
        origin_node_id: str | None = None,
        base_plan: Plan | None = None,
    ) -> PreferenceScores:
        return self.preference_scorer.score(
            objective=objective,
            profiles=profiles,
            attractions=attractions,
            live_context=live_context,
            knowledge=knowledge,
            park_graph=park_graph,
            origin_node_id=origin_node_id,
            base_plan=base_plan,
        )
