"""RoutingPort — structural contract for routing and walking estimate adapters.

RoutingClient (services/clients/routing_client.py) implements this today.
The planning core (ParkGraph, Optimizer, ConstraintChecker) depends on this
protocol only, never on concrete routing clients or external routing providers.
See P0-09 Done-when: "walk_minutes(a, b) is available through a port. The core
does not know which routing provider is used."

``WalkEstimator`` is the published side of the same adapter (P0-25
``data.get_walking_time``): it says *how* a number was obtained, and answers
``None`` instead of a default when it has no basis for one. It is a separate
protocol so the core keeps depending on ``walk_minutes`` alone.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

WalkBasis = Literal["identity", "curated", "estimated", "unknown"]
"""``identity``: same node. ``curated``: a reviewed matrix entry. ``estimated``:
computed from coordinates (straight line x detour factor). ``unknown``: no
route and no coordinates -- never filled with a default."""


@dataclass(frozen=True)
class WalkEstimate:
    minutes: float | None
    basis: WalkBasis

    def __post_init__(self) -> None:
        if (self.minutes is None) != (self.basis == "unknown"):
            raise ValueError("minutes is None exactly when the basis is 'unknown'")


class RoutingPort(Protocol):
    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        """Estimate walking time in minutes between two park planning nodes."""
        ...


@runtime_checkable
class WalkEstimator(Protocol):
    def estimate_walk(
        self, origin_node_id: str, destination_node_id: str
    ) -> WalkEstimate:
        """Walking minutes with their basis; ``unknown`` instead of any fallback default."""
        ...
