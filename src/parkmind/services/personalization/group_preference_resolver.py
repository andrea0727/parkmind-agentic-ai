"""
GroupPreferenceResolver — turns several GuestProfiles into one resolved
weight vector for PreferenceScorer.

Month-1 scope (deliberately simple — see docs/decisions/scope.md):
    resolved_weight[category] = weighted_average(guest_weights)
    then clamp so no guest's satisfaction floor is violated.

This is NOT a full fair-division solver — that's explicitly out of scope.
"""

from parkmind.models.guest_profile import GuestProfile


class ResolvedPreferences:
    def __init__(self, weights: dict[str, float], conflicts: list[dict]):
        self.weights = weights
        self.conflicts = conflicts


class GroupPreferenceResolver:
    def resolve(self, profiles: list[GuestProfile]) -> ResolvedPreferences:
        raise NotImplementedError
