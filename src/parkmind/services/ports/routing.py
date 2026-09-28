"""RoutingPort ? structural contract for routing and walking estimate adapters.

RoutingClient (services/clients/routing_client.py) implements this today.
The planning core (ParkGraph, Optimizer, ConstraintChecker) depends on this
protocol only, never on concrete routing clients or external routing providers.
See P0-09 Done-when: "walk_minutes(a, b) is available through a port. The core
does not know which routing provider is used."
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RoutingPort(Protocol):
    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        """Estimate walking time in minutes between two park planning nodes."""
        ...
