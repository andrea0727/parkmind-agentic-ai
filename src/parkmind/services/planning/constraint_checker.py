"""
ConstraintChecker — the functional safety gate. Every candidate plan must
pass through here before it can be proposed to the guest.

11 rules (§20, RuleId):
 1. OPENING_HOURS     6. WALKING_BUDGET
 2. HEIGHT            7. LUNCH_WINDOW
 3. SHOW_ARRIVAL      8. DEPARTURE
 4. MUST_DO           9. ACCESSIBILITY
 5. AVOID            10. RIDE_RESTRICTION
                     11. DATA_FRESHNESS

Rules 9-11 are safety- and integrity-bearing: they are never converted into
utility penalties, never partially satisfied, and never traded off against
preference score. A high preference/utility value can never buy back a
violation of these rules. Missing safety data (no accessibility check on
file, an unknown attraction status, a stale or incomplete LiveContext
snapshot) fails closed — the candidate is rejected rather than assumed safe.
"""

from datetime import timedelta

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    AttractionStatus,
    CheckResult,
    ConstraintViolation,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    RuleId,
    Stop,
    StopKind,
)

MAX_SNAPSHOT_AGE_MINUTES = 30
HEAT_SENSITIVITY_THRESHOLD_F = 90.0


class ConstraintChecker:
    """Validates a candidate plan against all 11 hard constraint rules."""

    def check(
        self,
        plan: Plan,
        constraints: PartyConstraints,
        accessibility: list[AccessibilityRequirements],
        attractions: dict[str, Attraction],
        park: Park,
        live_context: LiveContext,
    ) -> CheckResult:
        all_guest_ids = [guest.guest_id for guest in constraints.guests]
        accessibility_by_guest = {req.guest_id: req for req in accessibility}

        violations: list[ConstraintViolation] = []
        violations += self._check_data_freshness(plan, live_context)
        violations += self._check_opening_hours(plan, park)
        violations += self._check_height(plan, constraints, attractions, all_guest_ids)
        violations += self._check_show_arrival(plan, live_context)
        violations += self._check_must_do(plan, constraints, live_context)
        violations += self._check_avoid(plan, constraints)
        violations += self._check_walking_budget(plan, constraints)
        violations += self._check_lunch_window(plan, constraints)
        violations += self._check_departure(plan, constraints)
        violations += self._check_accessibility(
            plan, accessibility_by_guest, attractions, live_context, all_guest_ids
        )
        violations += self._check_ride_restriction(
            plan, accessibility_by_guest, live_context, all_guest_ids
        )

        return CheckResult(valid=not violations, violations=violations)

    @staticmethod
    def _served_guest_ids(stop: Stop, all_guest_ids: list[str]) -> list[str]:
        """Guests a stop applies to. Empty served_guests means the whole party."""
        return stop.served_guests if stop.served_guests else all_guest_ids

    @staticmethod
    def _temperature_during(stop: Stop, live_context: LiveContext) -> float | None:
        hour = stop.arrival_time.replace(minute=0, second=0, microsecond=0)
        for weather_hour in live_context.weather:
            if (
                weather_hour.timestamp.replace(minute=0, second=0, microsecond=0)
                == hour
            ):
                return weather_hour.temperature_f
        return None

    def _check_opening_hours(self, plan: Plan, park: Park) -> list[ConstraintViolation]:
        violations = []
        for stop in plan.stops:
            if (
                stop.arrival_time < park.opening_time
                or stop.departure_time > park.closing_time
            ):
                violations.append(
                    ConstraintViolation(
                        rule=RuleId.OPENING_HOURS,
                        message=f"Stop {stop.node_id} falls outside park operating hours",
                        stop_id=stop.node_id,
                        suggestion=f"Shift stop {stop.node_id} inside operating hours or remove it.",
                    )
                )
        return violations

    def _check_height(
        self,
        plan: Plan,
        constraints: PartyConstraints,
        attractions: dict[str, Attraction],
        all_guest_ids: list[str],
    ) -> list[ConstraintViolation]:
        violations = []
        guests_by_id = {guest.guest_id: guest for guest in constraints.guests}
        for stop in plan.stops:
            attraction = attractions.get(stop.node_id)
            if attraction is None or attraction.height_restriction_cm is None:
                continue
            for guest_id in self._served_guest_ids(stop, all_guest_ids):
                guest = guests_by_id.get(guest_id)
                if guest is None or guest.height_cm is None:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.HEIGHT,
                            message=(
                                f"Guest {guest_id} height unknown for height-restricted "
                                f"stop {stop.node_id}; failing closed"
                            ),
                            stop_id=stop.node_id,
                            suggestion=(
                                f"Remove guest {guest_id} from served_guests for stop "
                                f"{stop.node_id}; forbid the stop if no guests remain."
                            ),
                        )
                    )
                elif guest.height_cm < attraction.height_restriction_cm:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.HEIGHT,
                            message=(
                                f"Guest {guest_id} does not meet the height requirement "
                                f"for stop {stop.node_id}"
                            ),
                            stop_id=stop.node_id,
                            suggestion=(
                                f"Remove guest {guest_id} from served_guests for stop "
                                f"{stop.node_id}; forbid the stop if no guests remain."
                            ),
                        )
                    )
        return violations

    def _check_show_arrival(
        self, plan: Plan, live_context: LiveContext
    ) -> list[ConstraintViolation]:
        violations = []
        for stop in plan.stops:
            if stop.kind != StopKind.SHOW:
                continue
            showtimes = live_context.showtimes.get(stop.node_id, [])
            if not showtimes:
                continue
            if not any(
                stop.arrival_time <= t <= stop.departure_time for t in showtimes
            ):
                violations.append(
                    ConstraintViolation(
                        rule=RuleId.SHOW_ARRIVAL,
                        message=f"No scheduled showtime for {stop.node_id} within the stop window",
                        stop_id=stop.node_id,
                        suggestion=f"Adjust stop {stop.node_id} to match a scheduled showtime or remove it.",
                    )
                )
        return violations

    def _check_must_do(
        self, plan: Plan, constraints: PartyConstraints, live_context: LiveContext
    ) -> list[ConstraintViolation]:
        """
        Rule 4 [C23]: passes when every must-do is scheduled, or listed in
        unmet_must_do with an unavailability reason traceable to
        LiveContext.statuses for the whole remaining horizon. An OPERATING
        attraction listed as unmet is a violation.
        """
        violations = []
        planned = {stop.node_id for stop in plan.stops}
        unmet = set(plan.unmet_must_do)
        for attraction_id in constraints.must_do:
            if attraction_id in planned:
                continue
            if attraction_id in unmet:
                status = live_context.statuses.get(attraction_id)
                if status is None or status == AttractionStatus.OPERATING:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.MUST_DO,
                            message=(
                                f"Must-do {attraction_id} is listed as unmet but its status "
                                f"is {status.value if status else 'unknown'}, not confirmed "
                                f"unavailable for the whole horizon"
                            ),
                            suggestion=(
                                f"Reschedule {attraction_id} into the plan, or confirm its "
                                f"unavailability via LiveContext.statuses before marking it unmet."
                            ),
                        )
                    )
                continue
            violations.append(
                ConstraintViolation(
                    rule=RuleId.MUST_DO,
                    message=f"Must-do attraction {attraction_id} is missing from the plan",
                    suggestion=(
                        f"Insert a stop for {attraction_id}, or move it to unmet_must_do with "
                        f"a traceable unavailability reason."
                    ),
                )
            )
        return violations

    def _check_avoid(
        self, plan: Plan, constraints: PartyConstraints
    ) -> list[ConstraintViolation]:
        violations = []
        avoid = set(constraints.avoid)
        for stop in plan.stops:
            if stop.node_id in avoid:
                violations.append(
                    ConstraintViolation(
                        rule=RuleId.AVOID,
                        message=f"Plan includes avoided attraction {stop.node_id}",
                        stop_id=stop.node_id,
                        suggestion=f"Remove stop {stop.node_id} from the plan.",
                    )
                )
        return violations

    def _check_walking_budget(
        self, plan: Plan, constraints: PartyConstraints
    ) -> list[ConstraintViolation]:
        if constraints.party_walking_budget_minutes is None:
            return []
        if plan.total_walking_minutes > constraints.party_walking_budget_minutes:
            return [
                ConstraintViolation(
                    rule=RuleId.WALKING_BUDGET,
                    message=(
                        f"Plan walking total {plan.total_walking_minutes} minutes exceeds "
                        f"party budget of {constraints.party_walking_budget_minutes} minutes"
                    ),
                    suggestion="Drop the lowest-utility optional stop or insert a REST stop.",
                )
            ]
        return []

    def _check_lunch_window(
        self, plan: Plan, constraints: PartyConstraints
    ) -> list[ConstraintViolation]:
        if constraints.lunch_window is None:
            return []
        window = constraints.lunch_window
        for stop in plan.stops:
            if (
                stop.kind == StopKind.MEAL
                and window.start <= stop.arrival_time
                and stop.departure_time <= window.end
            ):
                return []
        return [
            ConstraintViolation(
                rule=RuleId.LUNCH_WINDOW,
                message="No meal stop scheduled within the party's lunch window",
                suggestion="Insert or move a MEAL stop into the lunch window.",
            )
        ]

    def _check_departure(
        self, plan: Plan, constraints: PartyConstraints
    ) -> list[ConstraintViolation]:
        if not plan.stops:
            return []
        last_departure = max(stop.departure_time for stop in plan.stops)
        if last_departure > constraints.departure_time:
            return [
                ConstraintViolation(
                    rule=RuleId.DEPARTURE,
                    message="Last stop departs after the party's required departure time",
                    suggestion="Trim or reschedule stops that fall after the departure time.",
                )
            ]
        return []

    def _check_accessibility(
        self,
        plan: Plan,
        accessibility_by_guest: dict[str, AccessibilityRequirements],
        attractions: dict[str, Attraction],
        live_context: LiveContext,
        all_guest_ids: list[str],
    ) -> list[ConstraintViolation]:
        violations = []
        for guest_id, req in accessibility_by_guest.items():
            guest_stops = sorted(
                (
                    stop
                    for stop in plan.stops
                    if guest_id in self._served_guest_ids(stop, all_guest_ids)
                ),
                key=lambda s: s.arrival_time,
            )
            if not guest_stops:
                continue

            if req.daily_walking_limit_minutes is not None:
                total_walking = sum(stop.walking_minutes for stop in guest_stops)
                if total_walking > req.daily_walking_limit_minutes:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.ACCESSIBILITY,
                            message=(
                                f"Guest {guest_id} walking total {total_walking} minutes exceeds "
                                f"their accessibility limit of {req.daily_walking_limit_minutes} minutes"
                            ),
                            suggestion="Drop the lowest-utility optional stop or insert a REST stop.",
                        )
                    )

            if req.rest_frequency_minutes is not None:
                last_rest_end = guest_stops[0].arrival_time
                for stop in guest_stops:
                    if stop.kind == StopKind.REST:
                        last_rest_end = stop.departure_time
                        continue
                    elapsed_minutes = (stop.departure_time - last_rest_end) / timedelta(
                        minutes=1
                    )
                    if elapsed_minutes > req.rest_frequency_minutes:
                        violations.append(
                            ConstraintViolation(
                                rule=RuleId.ACCESSIBILITY,
                                message=(
                                    f"Guest {guest_id} exceeds their required rest frequency "
                                    f"of {req.rest_frequency_minutes} minutes before stop {stop.node_id}"
                                ),
                                stop_id=stop.node_id,
                                suggestion=f"Insert a REST stop for guest {guest_id} before stop {stop.node_id}.",
                            )
                        )
                        break

            if req.heat_sensitivity:
                for stop in guest_stops:
                    attraction = attractions.get(stop.node_id)
                    if attraction is None or not attraction.outdoor:
                        continue
                    temperature_f = self._temperature_during(stop, live_context)
                    if temperature_f is None:
                        violations.append(
                            ConstraintViolation(
                                rule=RuleId.ACCESSIBILITY,
                                message=(
                                    f"No weather data to verify heat exposure for heat-sensitive "
                                    f"guest {guest_id} at outdoor stop {stop.node_id}; failing closed"
                                ),
                                stop_id=stop.node_id,
                                suggestion=(
                                    f"Reload LiveContext weather before proposing; remove guest "
                                    f"{guest_id} from served_guests until confirmed safe."
                                ),
                            )
                        )
                    elif temperature_f > HEAT_SENSITIVITY_THRESHOLD_F:
                        violations.append(
                            ConstraintViolation(
                                rule=RuleId.ACCESSIBILITY,
                                message=(
                                    f"Heat-sensitive guest {guest_id} is scheduled at outdoor stop "
                                    f"{stop.node_id} at {temperature_f}°F, above the "
                                    f"{HEAT_SENSITIVITY_THRESHOLD_F}°F threshold"
                                ),
                                stop_id=stop.node_id,
                                suggestion=(
                                    f"Remove guest {guest_id} from served_guests for stop "
                                    f"{stop.node_id} or substitute an indoor alternative."
                                ),
                            )
                        )
        return violations

    def _check_ride_restriction(
        self,
        plan: Plan,
        accessibility_by_guest: dict[str, AccessibilityRequirements],
        live_context: LiveContext,
        all_guest_ids: list[str],
    ) -> list[ConstraintViolation]:
        violations = []
        checks_by_key = {
            (check.attraction_id, check.guest_id): check
            for check in live_context.accessibility_results
        }
        for stop in plan.stops:
            for guest_id in self._served_guest_ids(stop, all_guest_ids):
                req = accessibility_by_guest.get(guest_id)
                if req is None or not (
                    req.ride_restrictions or req.mobility_requirements
                ):
                    continue

                check = checks_by_key.get((stop.node_id, guest_id))
                if check is None:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.RIDE_RESTRICTION,
                            message=(
                                f"No accessibility notice on file for guest {guest_id} at "
                                f"stop {stop.node_id}; failing closed"
                            ),
                            stop_id=stop.node_id,
                            suggestion=(
                                f"Remove guest {guest_id} from served_guests for stop "
                                f"{stop.node_id}; forbid the stop if no guests remain."
                            ),
                        )
                    )
                elif not check.eligible:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.RIDE_RESTRICTION,
                            message=(
                                f"Guest {guest_id} is not eligible for stop {stop.node_id} "
                                f"({check.conflicting_requirement})"
                            ),
                            stop_id=stop.node_id,
                            suggestion=(
                                f"Remove guest {guest_id} from served_guests for stop "
                                f"{stop.node_id}; forbid the stop if no guests remain."
                            ),
                        )
                    )
        return violations

    def _check_data_freshness(
        self, plan: Plan, live_context: LiveContext
    ) -> list[ConstraintViolation]:
        """
        Rule 11: status-dependent stops fail if the LiveContext snapshot age
        exceeds the freshness window, if per-guest accessibility coverage is
        incomplete, or if a stop's attraction status is unknown or not
        OPERATING (e.g. closed, down, under refurbishment).
        """
        violations = []
        if not live_context.coverage.accessibility_checks_complete:
            violations.append(
                ConstraintViolation(
                    rule=RuleId.DATA_FRESHNESS,
                    message="Accessibility checks incomplete for this snapshot; failing closed",
                    suggestion="Reload LiveContext once; fail closed if still incomplete.",
                )
            )

        if plan.stops:
            reference_time = min(stop.arrival_time for stop in plan.stops)
            age_minutes = (reference_time - live_context.retrieved_at) / timedelta(
                minutes=1
            )
            if age_minutes > MAX_SNAPSHOT_AGE_MINUTES:
                violations.append(
                    ConstraintViolation(
                        rule=RuleId.DATA_FRESHNESS,
                        message=(
                            f"LiveContext snapshot is {age_minutes:.0f} minutes old, "
                            f"exceeding the {MAX_SNAPSHOT_AGE_MINUTES}-minute freshness limit"
                        ),
                        suggestion="Reload LiveContext once; fail closed if still stale.",
                    )
                )

            for stop in plan.stops:
                if stop.kind != StopKind.ATTRACTION:
                    continue
                status = live_context.statuses.get(stop.node_id)
                if status is None:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.DATA_FRESHNESS,
                            message=f"Attraction status unknown for stop {stop.node_id}; failing closed",
                            stop_id=stop.node_id,
                            suggestion="Reload LiveContext once; drop the stop if status remains unknown.",
                        )
                    )
                elif status != AttractionStatus.OPERATING:
                    violations.append(
                        ConstraintViolation(
                            rule=RuleId.DATA_FRESHNESS,
                            message=f"Stop {stop.node_id} attraction status is {status.value}, not OPERATING",
                            stop_id=stop.node_id,
                            suggestion=f"Remove stop {stop.node_id} or replace it with an operating alternative.",
                        )
                    )
        return violations
