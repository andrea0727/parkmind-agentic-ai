"""
Signal Attribution Mapper.

Classifies planning decisions into STATED (explicit user constraints),
LEARNED (inferred preferences), or SYSTEM (operational optimization).
"""

from __future__ import annotations

from parkmind.core.contracts import (
    PartyConstraints,
    PreferenceSource,
    Provenance,
)
from parkmind.core.contracts.enums import AttributionType


class AttributionMapper:
    """Determines whether a stop decision derives from STATED or LEARNED signals."""

    def attribute_stop(
        self,
        node_id: str,
        constraints: PartyConstraints | None = None,
        provenance: Provenance | None = None,
        weight_sources: dict[str, PreferenceSource] | None = None,
    ) -> tuple[AttributionType, str]:
        """
        Classify a stop by its driving signal source.

        Returns (AttributionType, primary_reason_description).
        """
        # 1. Direct Stated Must-Do constraint
        if constraints and node_id in constraints.must_do:
            return (
                AttributionType.STATED,
                "Explicitly requested as a Must-Do attraction by the party.",
            )

        # 2. Check weight provenance mapping if provided
        if weight_sources and node_id in weight_sources:
            source = weight_sources[node_id]
            if source == PreferenceSource.STATED:
                return (
                    AttributionType.STATED,
                    "Selected based on explicitly declared ride category or theme preferences.",
                )
            if source == PreferenceSource.LEARNED:
                return (
                    AttributionType.LEARNED,
                    "Recommended based on inferred group affinity and historical tolerance.",
                )

        # 3. Fallback to System optimization
        return (
            AttributionType.SYSTEM,
            "Scheduled to minimize transit time and queue delay within operating hours.",
        )