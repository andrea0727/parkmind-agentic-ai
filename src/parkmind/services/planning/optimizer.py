"""
Optimizer — builds a candidate Plan given constraints, context and utility
scores. Month-1 scope: GreedyInsertionOptimizer only.

Algorithm overview
------------------
1. Anchor fixed-time stops (shows with showtimes, lunch in the lunch_window).
2. Insert must-do attractions greedily around the fixed anchors.
3. Fill remaining time with the highest marginal-utility attractions.
4. After every insertion, check the rest-frequency clock and insert a REST
   stop when time since the last break exceeds the most-sensitive guest's
   ``rest_frequency_minutes`` (read from AccessibilityRequirements, loaded
   per run from SessionStore — never from graph state [C19]).
5. Attractions that cannot fit before ``departure_time`` or that are
   DOWN/CLOSED are placed in ``unmet_must_do`` (graceful degradation [C18]).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    AttractionStatus,
    GroupObjective,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    Provenance,
    Stop,
    StopKind,
    TimeWindow,
)
from parkmind.services.planning.park_graph import ParkGraph

# ---------------------------------------------------------------------------
# Defaults — tunable but intentionally not persisted preferences
# ---------------------------------------------------------------------------
_DEFAULT_ATTRACTION_DURATION_MIN = 15.0
_DEFAULT_MEAL_DURATION_MIN = 45.0
_DEFAULT_REST_DURATION_MIN = 20.0
_DEFAULT_SHOW_DURATION_MIN = 25.0
_PARK_ENTRANCE_NODE = "__park_entrance__"


class GreedyInsertionOptimizer:
    """MVP optimizer for attraction sequencing.

    Pure-deterministic: given the same inputs it always produces the same
    ``Plan``.  No randomness, no LLM calls, no I/O — the caller
    (``BuildPlanUseCase``) is responsible for gathering the inputs.
    """

    def __init__(
        self,
        park_graph: ParkGraph,
        *,
        default_attraction_duration_minutes: float = _DEFAULT_ATTRACTION_DURATION_MIN,
        default_meal_duration_minutes: float = _DEFAULT_MEAL_DURATION_MIN,
        default_rest_duration_minutes: float = _DEFAULT_REST_DURATION_MIN,
        default_show_duration_minutes: float = _DEFAULT_SHOW_DURATION_MIN,
    ) -> None:
        self._graph = park_graph
        self._attraction_dur = default_attraction_duration_minutes
        self._meal_dur = default_meal_duration_minutes
        self._rest_dur = default_rest_duration_minutes
        self._show_dur = default_show_duration_minutes

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build_plan(
        self,
        constraints: PartyConstraints,
        context: LiveContext,
        utilities: dict[str, float],
        *,
        accessibility_reqs: Sequence[AccessibilityRequirements] | None = None,
        park: Park | None = None,
        catalog: Sequence[Attraction] | None = None,
        restaurant_node_ids: Sequence[str] | None = None,
        provenance: Provenance | None = None,
        group_objective: GroupObjective | None = None,
    ) -> Plan:
        """Build a candidate plan.

        Parameters
        ----------
        constraints:
            Party-level hard constraints (must_do, avoid, lunch_window …).
        context:
            Live snapshot — waits, statuses, showtimes.
        utilities:
            ``node_id → utility`` from PreferenceScorer.
        accessibility_reqs:
            Per-guest accessibility requirements (loaded per run from
            SessionStore, **never** from graph state — [C19]).
        park:
            Park schedule for opening/closing bounds.
        catalog:
            Known attraction metadata (used for duration estimation).
        restaurant_node_ids:
            Node ids of restaurant entities eligible for MEAL stops.
        provenance:
            Pre-built provenance; if *None* a minimal one is generated.
        group_objective:
            Resolved group objective; used for per-guest eligible sets.
        """
        start_time = self._resolve_start_time(park, context)
        end_time = constraints.departure_time

        # Effective rest frequency — minimum across all guests [C19]
        rest_freq = self._resolve_effective_rest_frequency(accessibility_reqs)

        # Index catalog for quick lookup
        catalog_index: dict[str, Attraction] = {}
        if catalog:
            catalog_index = {a.node_id: a for a in catalog}

        # Sets for filtering
        avoid_set = set(constraints.avoid)
        visited: set[str] = set()

        # Operating status filter
        operating_ids = {
            nid
            for nid, status in context.statuses.items()
            if status == AttractionStatus.OPERATING
        }

        # Per-guest eligible sets (from GroupObjective if available)
        per_guest_eligible: dict[str, set[str]] | None = None
        if group_objective and group_objective.per_guest_eligible:
            per_guest_eligible = {
                gid: set(ids)
                for gid, ids in group_objective.per_guest_eligible.items()
            }

        # Guest ids for served_guests on stops
        guest_ids = [g.guest_id for g in constraints.guests]

        # ---- Phase 0: Collect fixed-window anchors -----------------------
        anchors: list[Stop] = []

        # Show anchors from must_do that have showtimes
        for show_id in list(constraints.must_do):
            if context.showtimes.get(show_id):
                best_time = self._pick_best_showtime(
                    context.showtimes[show_id], start_time, end_time,
                )
                if best_time is not None:
                    dur = self._show_dur
                    anchors.append(Stop(
                        node_id=show_id,
                        kind=StopKind.SHOW,
                        arrival_time=best_time,
                        departure_time=best_time + timedelta(minutes=dur),
                        expected_wait_minutes=0.0,
                        walking_minutes=0.0,  # placeholder, recomputed later
                        utility=utilities.get(show_id, 0.0),
                        served_guests=guest_ids,
                    ))
                    visited.add(show_id)

        # Lunch anchor
        meal_anchor: Stop | None = None
        if constraints.lunch_window is not None:
            meal_anchor = self._create_meal_anchor(
                constraints.lunch_window,
                restaurant_node_ids,
                guest_ids,
            )

        # Sort anchors chronologically
        anchors.sort(key=lambda s: s.arrival_time)

        # ---- Phase 1: Build ordered candidate list -----------------------
        # Must-do first (excluding already-anchored shows), then by utility
        must_do_remaining = [
            nid for nid in constraints.must_do if nid not in visited
        ]
        other_candidates = sorted(
            (
                nid
                for nid in utilities
                if nid not in avoid_set
                and nid not in visited
                and nid not in set(must_do_remaining)
            ),
            key=lambda nid: utilities.get(nid, 0.0),
            reverse=True,
        )

        # ---- Phase 2: Greedy insertion -----------------------------------
        stops: list[Stop] = []
        unmet_must_do: list[str] = []
        cursor_time = start_time
        cursor_node = _PARK_ENTRANCE_NODE
        active_minutes_since_rest = 0.0
        total_wait = 0.0
        total_walk = 0.0

        # Merge must-do + others into a single insertion queue
        insertion_queue = must_do_remaining + other_candidates
        anchor_iter = iter(anchors)
        next_anchor: Stop | None = next(anchor_iter, None)

        # Track whether meal has been inserted
        meal_inserted = False

        for candidate_id in insertion_queue:
            # Skip if already visited, avoided, or not operating
            if candidate_id in visited:
                continue
            if candidate_id in avoid_set:
                if candidate_id in set(must_do_remaining):
                    unmet_must_do.append(candidate_id)
                continue
            if candidate_id not in operating_ids and candidate_id in set(must_do_remaining):
                unmet_must_do.append(candidate_id)
                continue
            if candidate_id not in operating_ids:
                continue

            # Check if we should insert anchors (shows) that fall before this
            # candidate's projected time
            while next_anchor is not None:
                walk_to_anchor = self._walk_time(cursor_node, next_anchor.node_id)
                earliest_anchor_start = cursor_time + timedelta(minutes=walk_to_anchor)

                if earliest_anchor_start <= next_anchor.arrival_time:
                    # Insert rest before anchor if needed
                    if rest_freq is not None:
                        cursor_time, cursor_node, active_minutes_since_rest = (
                            self._maybe_insert_rest(
                                stops, cursor_time, cursor_node,
                                active_minutes_since_rest, rest_freq,
                                walk_to_anchor, guest_ids, end_time,
                            )
                        )

                    anchor_walk = self._walk_time(cursor_node, next_anchor.node_id)
                    anchor_stop = next_anchor.model_copy(
                        update={"walking_minutes": anchor_walk},
                    )
                    # Adjust arrival if we arrive early — wait at the venue
                    actual_arrival = max(
                        cursor_time + timedelta(minutes=anchor_walk),
                        next_anchor.arrival_time,
                    )
                    if actual_arrival + timedelta(minutes=self._show_dur) > end_time:
                        break
                    anchor_stop = anchor_stop.model_copy(update={
                        "arrival_time": actual_arrival,
                        "departure_time": actual_arrival + timedelta(
                            minutes=self._show_dur,
                        ),
                        "walking_minutes": anchor_walk,
                    })
                    stops.append(anchor_stop)
                    total_walk += anchor_walk
                    step_time = anchor_walk + self._show_dur
                    active_minutes_since_rest += step_time
                    cursor_time = anchor_stop.departure_time
                    cursor_node = anchor_stop.node_id
                    next_anchor = next(anchor_iter, None)
                else:
                    break  # anchor is still in the future; insert candidates first

            # Insert meal if the lunch window has started and we haven't yet
            if (
                not meal_inserted
                and meal_anchor is not None
                and cursor_time >= meal_anchor.arrival_time - timedelta(minutes=15)
            ):
                cursor_time, cursor_node, active_minutes_since_rest, meal_inserted = (
                    self._insert_meal(
                        stops, cursor_time, cursor_node, meal_anchor,
                        active_minutes_since_rest, guest_ids, end_time,
                        total_walk,
                    )
                )
                total_walk += stops[-1].walking_minutes if meal_inserted and stops else 0.0

            # Estimate cost to reach this candidate
            walk_min = self._walk_time(cursor_node, candidate_id)
            wait_min = self._get_wait(candidate_id, context)
            duration = self._get_duration(candidate_id, catalog_index)

            projected_end = cursor_time + timedelta(
                minutes=walk_min + wait_min + duration,
            )
            if projected_end > end_time:
                if candidate_id in set(must_do_remaining):
                    unmet_must_do.append(candidate_id)
                continue

            # Check if a rest is needed before this stop
            if rest_freq is not None:
                cursor_time, cursor_node, active_minutes_since_rest = (
                    self._maybe_insert_rest(
                        stops, cursor_time, cursor_node,
                        active_minutes_since_rest, rest_freq,
                        walk_min + wait_min + duration, guest_ids, end_time,
                    )
                )
                # Recompute walk after potential rest
                walk_min = self._walk_time(cursor_node, candidate_id)
                projected_end = cursor_time + timedelta(
                    minutes=walk_min + wait_min + duration,
                )
                if projected_end > end_time:
                    if candidate_id in set(must_do_remaining):
                        unmet_must_do.append(candidate_id)
                    continue

            arrival = cursor_time + timedelta(minutes=walk_min)
            departure = arrival + timedelta(minutes=wait_min + duration)

            # Determine served_guests based on eligibility
            served = self._compute_served_guests(
                candidate_id, guest_ids, per_guest_eligible,
            )

            stop = Stop(
                node_id=candidate_id,
                kind=StopKind.ATTRACTION,
                arrival_time=arrival,
                departure_time=departure,
                expected_wait_minutes=wait_min,
                walking_minutes=walk_min,
                utility=utilities.get(candidate_id, 0.0),
                served_guests=served,
            )
            stops.append(stop)
            visited.add(candidate_id)
            total_wait += wait_min
            total_walk += walk_min
            step_time = walk_min + wait_min + duration
            active_minutes_since_rest += step_time
            cursor_time = departure
            cursor_node = candidate_id

        # Flush remaining anchors
        while next_anchor is not None:
            anchor_walk = self._walk_time(cursor_node, next_anchor.node_id)
            actual_arrival = max(
                cursor_time + timedelta(minutes=anchor_walk),
                next_anchor.arrival_time,
            )
            if actual_arrival + timedelta(minutes=self._show_dur) > end_time:
                break
            anchor_stop = next_anchor.model_copy(update={
                "arrival_time": actual_arrival,
                "departure_time": actual_arrival + timedelta(
                    minutes=self._show_dur,
                ),
                "walking_minutes": anchor_walk,
            })
            stops.append(anchor_stop)
            total_walk += anchor_walk
            cursor_time = anchor_stop.departure_time
            cursor_node = anchor_stop.node_id
            next_anchor = next(anchor_iter, None)

        # Insert meal at end if still not inserted and there's time
        if not meal_inserted and meal_anchor is not None:
            cursor_time, cursor_node, active_minutes_since_rest, meal_inserted = (
                self._insert_meal(
                    stops, cursor_time, cursor_node, meal_anchor,
                    active_minutes_since_rest, guest_ids, end_time,
                    total_walk,
                )
            )
            if meal_inserted and stops:
                total_walk += stops[-1].walking_minutes

        # ---- Phase 3: Totals and satisfaction ----------------------------
        objective_value = sum(s.utility for s in stops)
        per_guest_satisfaction = self._calculate_guest_satisfaction(
            stops, constraints, group_objective,
        )

        plan_provenance = provenance or self._minimal_provenance(context)

        return Plan(
            plan_id=f"plan_{uuid.uuid4().hex[:12]}",
            version=1,
            stops=stops,
            total_wait_minutes=total_wait,
            total_walking_minutes=total_walk,
            objective_value=objective_value,
            per_guest_satisfaction=per_guest_satisfaction,
            unmet_must_do=unmet_must_do,
            provenance=plan_provenance,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_start_time(
        self, park: Park | None, context: LiveContext,
    ) -> datetime:
        if park is not None:
            return park.opening_time
        return context.retrieved_at

    @staticmethod
    def _resolve_effective_rest_frequency(
        accessibility_reqs: Sequence[AccessibilityRequirements] | None,
    ) -> int | None:
        """Minimum ``rest_frequency_minutes`` across all guests.

        Returns *None* when no guest has a rest requirement, meaning
        rest insertion is disabled for this run.
        """
        if not accessibility_reqs:
            return None
        values = [
            req.rest_frequency_minutes
            for req in accessibility_reqs
            if req.rest_frequency_minutes is not None
            and req.rest_frequency_minutes > 0
        ]
        return min(values) if values else None

    def _walk_time(self, origin: str, destination: str) -> float:
        if origin == destination:
            return 0.0
        if origin == _PARK_ENTRANCE_NODE or destination == _PARK_ENTRANCE_NODE:
            return 5.0  # flat default for park entrance
        return self._graph.walk_minutes(origin, destination)

    @staticmethod
    def _get_wait(node_id: str, context: LiveContext) -> float:
        if node_id in context.waits:
            return context.waits[node_id].wait_minutes
        return 0.0

    def _get_duration(
        self, node_id: str, catalog_index: dict[str, Attraction],
    ) -> float:
        """Ride/experience duration estimate (excludes wait time)."""
        if node_id in catalog_index:
            attr = catalog_index[node_id]
            # Shows have a known duration slot; attractions use the default
            if attr.category.value == "SHOW":
                return self._show_dur
        return self._attraction_dur

    @staticmethod
    def _pick_best_showtime(
        times: list[datetime],
        start: datetime,
        end: datetime,
    ) -> datetime | None:
        """Earliest showtime that falls inside ``[start, end)``."""
        valid = [t for t in sorted(times) if start <= t < end]
        return valid[0] if valid else None

    def _create_meal_anchor(
        self,
        window: TimeWindow,
        restaurant_node_ids: Sequence[str] | None,
        guest_ids: list[str],
    ) -> Stop:
        """Build a MEAL stop placeholder inside the lunch window."""
        node = (
            restaurant_node_ids[0]
            if restaurant_node_ids
            else "__restaurant__"
        )
        arrival = window.start
        departure = arrival + timedelta(minutes=self._meal_dur)
        # Clamp departure to window end
        departure = min(departure, window.end)
        return Stop(
            node_id=node,
            kind=StopKind.MEAL,
            arrival_time=arrival,
            departure_time=departure,
            expected_wait_minutes=0.0,
            walking_minutes=0.0,
            utility=0.0,
            served_guests=guest_ids,
        )

    def _maybe_insert_rest(
        self,
        stops: list[Stop],
        cursor_time: datetime,
        cursor_node: str,
        active_minutes: float,
        rest_freq: int,
        upcoming_step_minutes: float,
        guest_ids: list[str],
        end_time: datetime,
    ) -> tuple[datetime, str, float]:
        """Insert a REST stop if accumulated active time would exceed the
        rest-frequency threshold once the upcoming step completes.

        Returns updated ``(cursor_time, cursor_node, active_minutes)``.
        """
        if active_minutes + upcoming_step_minutes <= rest_freq:
            return cursor_time, cursor_node, active_minutes

        # Not enough time for a rest? Skip it.
        rest_end = cursor_time + timedelta(minutes=self._rest_dur)
        if rest_end > end_time:
            return cursor_time, cursor_node, active_minutes

        rest_stop = Stop(
            node_id=cursor_node,  # rest at current location
            kind=StopKind.REST,
            arrival_time=cursor_time,
            departure_time=rest_end,
            expected_wait_minutes=0.0,
            walking_minutes=0.0,
            utility=0.0,
            served_guests=guest_ids,
        )
        stops.append(rest_stop)
        return rest_end, cursor_node, 0.0

    def _insert_meal(
        self,
        stops: list[Stop],
        cursor_time: datetime,
        cursor_node: str,
        meal_anchor: Stop,
        active_minutes: float,
        guest_ids: list[str],
        end_time: datetime,
        total_walk: float,
    ) -> tuple[datetime, str, float, bool]:
        """Insert the MEAL stop, adjusting timing to current cursor.

        Returns ``(cursor_time, cursor_node, active_minutes, inserted)``.
        """
        walk_min = self._walk_time(cursor_node, meal_anchor.node_id)
        arrival = cursor_time + timedelta(minutes=walk_min)
        departure = arrival + timedelta(minutes=self._meal_dur)

        if departure > end_time:
            return cursor_time, cursor_node, active_minutes, False

        meal_stop = Stop(
            node_id=meal_anchor.node_id,
            kind=StopKind.MEAL,
            arrival_time=arrival,
            departure_time=departure,
            expected_wait_minutes=0.0,
            walking_minutes=walk_min,
            utility=0.0,
            served_guests=guest_ids,
        )
        stops.append(meal_stop)
        return departure, meal_anchor.node_id, 0.0, True

    @staticmethod
    def _compute_served_guests(
        node_id: str,
        all_guests: list[str],
        per_guest_eligible: dict[str, set[str]] | None,
    ) -> list[str]:
        """Guests who can actually ride/experience this attraction."""
        if per_guest_eligible is None:
            return list(all_guests)
        return [
            gid for gid in all_guests
            if gid not in per_guest_eligible or node_id in per_guest_eligible[gid]
        ]

    @staticmethod
    def _calculate_guest_satisfaction(
        stops: list[Stop],
        constraints: PartyConstraints,
        group_objective: GroupObjective | None,
    ) -> dict[str, float]:
        """Normalized per-guest satisfaction [C20].

        For each guest, satisfaction = (utility obtained from stops the guest
        was served) / (total utility of the guest's eligible set).
        Falls back to a uniform ratio when ``GroupObjective`` is absent.
        """
        guest_ids = [g.guest_id for g in constraints.guests]

        # Utility earned per guest
        earned: dict[str, float] = {gid: 0.0 for gid in guest_ids}
        for s in stops:
            for gid in s.served_guests:
                if gid in earned:
                    earned[gid] += s.utility

        if group_objective and group_objective.per_guest_eligible:
            satisfaction: dict[str, float] = {}
            for gid in guest_ids:
                eligible_count = len(
                    group_objective.per_guest_eligible.get(gid, []),
                )
                if eligible_count == 0:
                    satisfaction[gid] = 1.0  # nothing to satisfy
                else:
                    # Count how many eligible attractions were visited
                    eligible_set = set(
                        group_objective.per_guest_eligible.get(gid, []),
                    )
                    visited_eligible = sum(
                        1
                        for s in stops
                        if s.node_id in eligible_set and gid in s.served_guests
                    )
                    satisfaction[gid] = visited_eligible / eligible_count
            return satisfaction

        # Fallback: ratio of attractions served vs total stops
        total_stops = max(
            sum(1 for s in stops if s.kind == StopKind.ATTRACTION), 1,
        )
        return {
            gid: sum(
                1 for s in stops
                if s.kind == StopKind.ATTRACTION and gid in s.served_guests
            ) / total_stops
            for gid in guest_ids
        }

    @staticmethod
    def _minimal_provenance(context: LiveContext) -> Provenance:
        """Build a bare-minimum Provenance when the caller doesn't provide one."""
        return Provenance(
            snapshot_id=context.snapshot_id,
            retrieved_at=context.retrieved_at,
            forecast_strategy="current_wait",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="auto",
            preference_model_version="auto",
        )
