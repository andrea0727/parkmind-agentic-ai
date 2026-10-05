"""Use case: thin wrapper around GroupPreferenceResolver.

Keeps services.personalization (pure deterministic core, no I/O) decoupled
from the agents/graph layer: agents may only reach it through this use case
(see .importlinter "agents-forbidden-imports").
"""

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestProfile,
    LiveContext,
    PartyConstraints,
)
from parkmind.services.personalization.group_preference_resolver import (
    GroupPreferenceResolver,
)


class ResolveGroupPreferencesUseCase:
    def __init__(
        self, group_preference_resolver: GroupPreferenceResolver | None = None
    ):
        self.group_preference_resolver = (
            group_preference_resolver or GroupPreferenceResolver()
        )

    def execute(
        self,
        *,
        guests: list[Guest],
        profiles: list[GuestProfile],
        accessibility: list[AccessibilityRequirements],
        attractions: list[Attraction],
        party_constraints: PartyConstraints,
        live_context: LiveContext,
        fairness: FairnessConfig,
        objective_version: str = "1",
    ) -> GroupObjective:
        return self.group_preference_resolver.resolve(
            guests=guests,
            profiles=profiles,
            accessibility=accessibility,
            attractions=attractions,
            party_constraints=party_constraints,
            live_context=live_context,
            fairness=fairness,
            objective_version=objective_version,
        )
