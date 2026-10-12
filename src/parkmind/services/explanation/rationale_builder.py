"""
Rationale Builder module.

Constructs detailed StopRationale instances for every stop in a candidate plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Sequence

from parkmind.core.contracts import (
    LiveContext,
    PartyConstraints,
    Plan,
    PreferenceSource,
    Provenance,
    Stop,
    StopKind,
)
from parkmind.core.contracts.enums import AttributionType
from parkmind.core.contracts.models import StopRationale
from parkmind.services.explanation.attribution_mapper import AttributionMapper


class RationaleBuilder:
    """Builds stop-by-stop rationales for plan explanations."""

    def __init__(self, mapper: AttributionMapper | None = None) -> None:
        self._mapper = mapper or AttributionMapper()

    def build_rationales(
        self,
        plan: Plan,
        constraints: PartyConstraints | None = None,
        provenance: Provenance | None = None,
        catalog_names: Mapping[str, str] | None = None,
        weight_sources: dict[str, PreferenceSource] | None = None,
    ) -> list[StopRationale]:
        """Generate structured rationale items for every stop in the plan."""
        catalog = catalog_names or {}
        rationales: list[StopRationale] = []

        for stop in plan.stops:
            name = catalog.get(stop.node_id, stop.node_id)
            factors: list[str] = []

            if stop.kind == StopKind.MEAL:
                attribution = AttributionType.STATED if constraints and constraints.lunch_window else AttributionType.SYSTEM
                primary_reason = "Scheduled within the designated lunch window."
            elif stop.kind == StopKind.REST:
                attribution = AttributionType.STATED
                primary_reason = "Scheduled to comply with guest rest frequency requirements."
            elif stop.kind == StopKind.SHOW:
                attribution = AttributionType.STATED if (constraints and stop.node_id in constraints.must_do) else AttributionType.SYSTEM
                primary_reason = f"Aligned with scheduled showtimes for {name}."
            else:
                attribution, primary_reason = self._mapper.attribute_stop(
                    node_id=stop.node_id,
                    constraints=constraints,
                    provenance=provenance,
                    weight_sources=weight_sources,
                )

            if stop.expected_wait_minutes > 0:
                factors.append(f"Expected wait time: {stop.expected_wait_minutes:.0f} min")
            if stop.walking_minutes > 0:
                factors.append(f"Transit walking time: {stop.walking_minutes:.0f} min")

            rationales.append(
                StopRationale(
                    node_id=stop.node_id,
                    arrival_time=stop.arrival_time,
                    departure_time=stop.departure_time,
                    primary_reason=primary_reason,
                    attribution=attribution,
                    wait_minutes=stop.expected_wait_minutes,
                    walk_minutes=stop.walking_minutes,
                    contributing_factors=factors,
                )
            )

        return rationales