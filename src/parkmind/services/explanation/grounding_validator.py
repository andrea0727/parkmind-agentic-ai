"""
Numerical Grounding Validator module.

Extracts ground-truth numeric metrics and clock times from validated plan payloads
and verifies that narrative text introduces no ungrounded numeric literals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timezone

from parkmind.core.contracts import LiveContext, Plan, PlanDiff

# Default fallback timezone if park tz is not attached to datetimes
_PARK_TZ = timezone.utc


@dataclass
class GroundingContext:
    """Contains all numeric values and clock times allowed in an explanation."""

    allowed_numbers: set[float] = field(default_factory=set)
    allowed_times: set[str] = field(default_factory=set)

    @classmethod
    def from_inputs(
        cls,
        plan: Plan,
        diff: PlanDiff | None = None,
        context: LiveContext | None = None,
    ) -> GroundingContext:
        gc = cls()

        # Plan-level metrics
        gc.allowed_numbers.update(
            {
                float(len(plan.stops)),
                round(plan.total_wait_minutes, 1),
                float(round(plan.total_wait_minutes)),
                round(plan.total_walking_minutes, 1),
                float(round(plan.total_walking_minutes)),
                float(len(plan.unmet_must_do)),
            }
        )

        # Stop-level metrics and times
        for stop in plan.stops:
            gc.allowed_numbers.update(
                {
                    round(stop.expected_wait_minutes, 1),
                    float(round(stop.expected_wait_minutes)),
                    round(stop.walking_minutes, 1),
                    float(round(stop.walking_minutes)),
                }
            )

            # Extract localized hour/minute representations
            tz = stop.arrival_time.tzinfo or _PARK_TZ
            arr = stop.arrival_time.astimezone(tz)
            dep = stop.departure_time.astimezone(tz)

            gc.allowed_times.add(f"{arr.hour}:{arr.minute:02d}")
            gc.allowed_times.add(f"{arr.hour:02d}:{arr.minute:02d}")
            gc.allowed_times.add(f"{dep.hour}:{dep.minute:02d}")
            gc.allowed_times.add(f"{dep.hour:02d}:{dep.minute:02d}")

            gc.allowed_numbers.add(float(arr.hour))
            gc.allowed_numbers.add(float(dep.hour))

        # Diff metrics (if present during replanning)
        if diff:
            gc.allowed_numbers.update(
                {
                    float(len(diff.stops_added)),
                    float(len(diff.stops_removed)),
                    float(len(diff.stops_reordered)),
                    float(len(diff.stops_modified)),
                }
            )

        return gc


class NumericalGroundingValidator:
    """Validates that no ungrounded numeric literals exist in narrative text."""

    _TIME_REGEX = re.compile(r"\b(\d{1,2}:\d{2})\b")
    _NUMBER_REGEX = re.compile(r"\b(\d+(?:\.\d+)?)\b")

    def validate(self, text: str, context: GroundingContext) -> tuple[bool, list[str]]:
        """
        Verify narrative text against allowable grounding context.

        Returns (is_valid, list_of_ungrounded_tokens).
        """
        ungrounded: list[str] = []

        # 1. Validate Clock Times (e.g., "10:30", "09:15")
        time_matches = self._TIME_REGEX.findall(text)
        for t in time_matches:
            if t not in context.allowed_times:
                ungrounded.append(f"time:{t}")

        # Strip times before processing raw standalone numbers
        text_without_times = self._TIME_REGEX.sub(" ", text)

        # 2. Validate Numeric Literals
        for num_str in self._NUMBER_REGEX.findall(text_without_times):
            val = float(num_str)
            if not self._is_number_grounded(val, context.allowed_numbers):
                ungrounded.append(f"num:{num_str}")

        return (len(ungrounded) == 0, ungrounded)

    @staticmethod
    def _is_number_grounded(val: float, allowed: set[float]) -> bool:
        return any(
            abs(val - a) < 1e-4 or abs(round(val) - a) < 1e-4 for a in allowed
        )