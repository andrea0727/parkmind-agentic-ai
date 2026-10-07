"""
Optimizer — builds a candidate Plan given constraints, context and utility
scores. Month-1 scope: GreedyInsertionOptimizer only.

Algorithm overview
------------------
1. Anchor fixed-time stops (shows with valid showtimes, lunch in the lunch_window).
2. Insert must-do attractions greedily around the fixed anchors by marginal utility / cost.
3. Fill remaining time with the highest marginal-utility / cost attractions.
4. After every insertion, check the rest-frequency clock and insert a REST
   stop when time since the last break exceeds the most-sensitive guest's
   ``rest_frequency_minutes`` (read from AccessibilityRequirements, loaded
   per run from SessionStore — never from graph state [C19]).
5. Attractions that cannot fit before ``departure_time`` or that are
   DOWN/CLOSED/avoided or have no valid showtimes are placed in ``unmet_must_do``
   (graceful degradation [C18]).

Waits (issue #75): with a ``ForecastService``, each attraction is charged the
wait expected at the time the party would reach it (section 18), not the wait
posted when the snapshot was taken. When no strategy has a reading, the stop is
charged the curated ``typical_wait_minutes`` (labelled ``typical_wait``), never
0; an attraction with neither is not scheduled. Without a forecast service the
posted wait is used as before.

Walking cost is the real walk from the previous stop. Utilities from
``PreferenceScorer`` should be computed without an ``origin_node_id``, so the
walk is not charged twice on two different bases.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    AttractionCategory,
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
from parkmind.services.personalization.preference_scorer import (
    PreferenceScores,
)
from parkmind.services.personalization.preference_scorer import (
    per_guest_satisfaction as scorer_satisfaction,
)
from parkmind.services.planning.forecast_service import (
    TYPICAL_WAIT,
    ForecastService,
    forecast_data_sources,
    forecast_strategy_label,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import WaitForecast

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_WaitSource = WaitForecast | str
"""Where a charged wait came from: a forecast, ``TYPICAL_WAIT``, or the posted wait."""
_POSTED_WAIT = "current_wait"


def _revalidated(model: _ModelT, **changes: object) -> _ModelT:
    """``model`` with ``changes``, run through the contract's validators again.

    ``model_copy(update=...)`` skips validation; this keeps every contract check
    (e.g. timezone-aware datetimes) on the values the optimizer sets.
    """
    return type(model).model_validate({**model.model_dump(), **changes})

# ---------------------------------------------------------------------------
# Defaults — tunable but intentionally not persisted preferences
# ---------------------------------------------------------------------------
_DEFAULT_ATTRACTION_DURATION_MIN = 15.0
_DEFAULT_MEAL_DURATION_MIN = 45.0
_DEFAULT_REST_DURATION_MIN = 20.0
_DEFAULT_SHOW_DURATION_MIN = 25.0
_LUNCH_WINDOW_EARLY_BUFFER_MIN = 15.0
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
        forecast_service: ForecastService | None = None,
        now: datetime | None = None,
        scores: PreferenceScores | None = None,
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
        forecast_service:
            Expected waits per stop time (P0-18). Without it, the posted wait
            is charged at every hour, as before.
        now:
            The planning moment, required with ``forecast_service``; the
            optimizer never reads the clock.
        scores:
            The ``PreferenceScorer`` result the utilities came from. With it,
            per-guest satisfaction is the scorer's (each guest against the
            best of their own eligible set, C20) and the plan's
            ``preference_model_version`` is the scorer's version.
        """
        if forecast_service is not None and now is None:
            raise ValueError("now is required when a forecast_service is given")
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise ValueError("now must be timezone-aware")
        if scores is not None:
            party = {g.guest_id for g in constraints.guests}
            scored = set(scores.per_guest)
            if scored != party:
                raise ValueError(
                    "scores and constraints describe different parties: "
                    f"only scored {sorted(scored - party)}, only in constraints {sorted(party - scored)}"
                )
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
        must_do_set = set(constraints.must_do)
        visited: set[str] = set()

        # Operating status filter
        operating_ids = {
            nid
            for nid, status in context.statuses.items()
            if status == AttractionStatus.OPERATING
        }

        # Identify all show nodes (entities with showtimes or category SHOW)
        show_node_ids = set(context.showtimes.keys())
        if catalog:
            show_node_ids |= {
                a.node_id for a in catalog if a.category == AttractionCategory.SHOW
            }

        # Per-guest eligible sets (from GroupObjective if available)
        per_guest_eligible: dict[str, set[str]] | None = None
        if group_objective and group_objective.per_guest_eligible:
            per_guest_eligible = {
                gid: set(ids) for gid, ids in group_objective.per_guest_eligible.items()
            }

        # Guest ids for served_guests on stops
        guest_ids = [g.guest_id for g in constraints.guests]

        # ---- Phase 0: Collect fixed-window anchors -----------------------
        anchors: list[Stop] = []

        # Show anchors from must_do that have showtimes
        for show_id in constraints.must_do:
            if context.showtimes.get(show_id):
                best_time = self._pick_best_showtime(
                    context.showtimes[show_id],
                    start_time,
                    end_time,
                )
                if best_time is not None:
                    dur = self._show_dur
                    anchors.append(
                        Stop(
                            node_id=show_id,
                            kind=StopKind.SHOW,
                            arrival_time=best_time,
                            departure_time=best_time + timedelta(minutes=dur),
                            expected_wait_minutes=0.0,
                            walking_minutes=0.0,  # placeholder, recomputed later
                            utility=utilities.get(show_id, 0.0),
                            served_guests=guest_ids,
                        )
                    )

        # Lunch anchor placeholder
        meal_anchor: Stop | None = None
        if constraints.lunch_window is not None:
            meal_anchor = self._create_meal_anchor(
                constraints.lunch_window,
                restaurant_node_ids,
                guest_ids,
            )

        # Sort anchors chronologically
        anchors.sort(key=lambda s: s.arrival_time)

        # ---- Phase 1 & 2: Dynamic Greedy Insertion -----------------------
        stops: list[Stop] = []
        charged_forecasts: list[WaitForecast] = []
        typical_wait_used = False
        # One read per (attraction, arrival) per run: what a candidate was ranked on is
        # exactly what it is charged when inserted, even if a strategy answers
        # differently on a second call, and a failing source is asked (and logged) once.
        wait_readings: dict[tuple[str, datetime], tuple[float, _WaitSource] | None] = {}

        def read_wait(node_id: str, arrival: datetime) -> tuple[float, _WaitSource] | None:
            key = (node_id, arrival)
            if key not in wait_readings:
                wait_readings[key] = self._wait_at(
                    node_id, arrival, context, catalog_index, forecast_service, now
                )
            return wait_readings[key]
        cursor_time = start_time
        cursor_node = _PARK_ENTRANCE_NODE
        active_minutes_since_rest = 0.0

        anchor_idx = 0
        meal_inserted = False

        while True:
            # 1. Check if next anchor (show) is due now
            if anchor_idx < len(anchors):
                curr_anchor = anchors[anchor_idx]
                walk_to_anchor = self._walk_time(cursor_node, curr_anchor.node_id)
                earliest_arrival = cursor_time + timedelta(minutes=walk_to_anchor)
                if earliest_arrival >= curr_anchor.arrival_time:
                    # Anchor is due now
                    if rest_freq is not None:
                        cursor_time, cursor_node, active_minutes_since_rest = (
                            self._maybe_insert_rest(
                                stops,
                                cursor_time,
                                cursor_node,
                                active_minutes_since_rest,
                                rest_freq,
                                walk_to_anchor + self._show_dur,
                                guest_ids,
                                end_time,
                            )
                        )
                        walk_to_anchor = self._walk_time(
                            cursor_node, curr_anchor.node_id
                        )
                    actual_arrival = max(
                        cursor_time + timedelta(minutes=walk_to_anchor),
                        curr_anchor.arrival_time,
                    )
                    show_departure = actual_arrival + timedelta(minutes=self._show_dur)
                    if show_departure <= end_time:
                        anchor_stop = curr_anchor.model_copy(
                            update={
                                "arrival_time": actual_arrival,
                                "departure_time": show_departure,
                                "walking_minutes": walk_to_anchor,
                            }
                        )
                        stops.append(anchor_stop)
                        visited.add(curr_anchor.node_id)
                        cursor_time = show_departure
                        cursor_node = curr_anchor.node_id
                        active_minutes_since_rest += walk_to_anchor + self._show_dur
                    anchor_idx += 1
                    continue

            # 2. Check if meal is due now
            if meal_anchor is not None and not meal_inserted:
                lunch_win = constraints.lunch_window
                assert lunch_win is not None
                walk_to_meal = self._walk_time(cursor_node, meal_anchor.node_id)
                earliest_meal_arrival = cursor_time + timedelta(minutes=walk_to_meal)
                if earliest_meal_arrival >= lunch_win.start - timedelta(
                    minutes=_LUNCH_WINDOW_EARLY_BUFFER_MIN
                ):
                    # We are within or at the lunch window
                    actual_arrival = max(earliest_meal_arrival, lunch_win.start)
                    if actual_arrival <= lunch_win.end:
                        meal_departure = actual_arrival + timedelta(
                            minutes=self._meal_dur
                        )
                        if meal_departure <= end_time:
                            meal_stop = Stop(
                                node_id=meal_anchor.node_id,
                                kind=StopKind.MEAL,
                                arrival_time=actual_arrival,
                                departure_time=meal_departure,
                                expected_wait_minutes=0.0,
                                walking_minutes=walk_to_meal,
                                utility=0.0,
                                served_guests=guest_ids,
                            )
                            stops.append(meal_stop)
                            cursor_time = meal_departure
                            cursor_node = meal_anchor.node_id
                            active_minutes_since_rest = 0.0
                    meal_inserted = True
                    continue

            # 3. Find candidates that fit before upcoming deadlines
            next_anchor = anchors[anchor_idx] if anchor_idx < len(anchors) else None
            lunch_win = (
                constraints.lunch_window
                if (meal_anchor is not None and not meal_inserted)
                else None
            )

            candidate_must_dos = [
                nid
                for nid in constraints.must_do
                if nid not in visited
                and nid not in avoid_set
                and nid in operating_ids
                and nid not in show_node_ids
            ]
            other_candidates = [
                nid
                for nid in utilities
                if nid not in visited
                and nid not in avoid_set
                and nid in operating_ids
                and nid not in show_node_ids
                and nid not in must_do_set
            ]

            fitting_must_dos: list[tuple[float, float, float, str]] = []
            fitting_others: list[tuple[float, float, float, str]] = []

            for pool, target_list in [
                (candidate_must_dos, fitting_must_dos),
                (other_candidates, fitting_others),
            ]:
                for cid in pool:
                    walk_min = self._walk_time(cursor_node, cid)
                    reading = read_wait(cid, cursor_time + timedelta(minutes=walk_min))
                    if reading is None:
                        continue  # no forecast and no typical wait: never charged 0
                    wait_min = reading[0]
                    dur_min = self._get_duration(cid, catalog_index)
                    step_min = walk_min + wait_min + dur_min

                    rest_min = 0.0
                    if (
                        rest_freq is not None
                        and active_minutes_since_rest + step_min > rest_freq
                    ):
                        rest_min = self._rest_dur
                        # A rest first moves the arrival later: read the wait then.
                        reading = read_wait(
                            cid, cursor_time + timedelta(minutes=rest_min + walk_min)
                        )
                        if reading is None:
                            continue
                        wait_min = reading[0]
                        step_min = walk_min + wait_min + dur_min

                    total_dur = rest_min + step_min
                    cand_departure = cursor_time + timedelta(minutes=total_dur)

                    # Check deadline 1: departure_time
                    if cand_departure > end_time:
                        continue

                    # Check deadline 2: next_anchor
                    if next_anchor is not None:
                        walk_after = self._walk_time(cid, next_anchor.node_id)
                        if (
                            cand_departure + timedelta(minutes=walk_after)
                            > next_anchor.arrival_time
                        ):
                            continue

                    # Check deadline 3: lunch window
                    if (
                        lunch_win is not None
                        and meal_anchor is not None
                        and cursor_time < lunch_win.start
                    ):
                        walk_to_meal = self._walk_time(cid, meal_anchor.node_id)
                        if (
                            cand_departure + timedelta(minutes=walk_to_meal)
                            > lunch_win.end
                        ):
                            continue

                    util = utilities.get(cid, 0.0)
                    cost = max(step_min, 0.1)
                    ratio = util / cost
                    target_list.append((ratio, util, -total_dur, cid))

            chosen_id: str | None = None
            if fitting_must_dos:
                fitting_must_dos.sort(reverse=True)
                chosen_id = fitting_must_dos[0][3]
            elif fitting_others:
                fitting_others.sort(reverse=True)
                if fitting_others[0][1] >= 0.0:
                    chosen_id = fitting_others[0][3]

            # 4. If a candidate was chosen, insert it
            if chosen_id is not None:
                walk_min = self._walk_time(cursor_node, chosen_id)
                reading = read_wait(chosen_id, cursor_time + timedelta(minutes=walk_min))
                if reading is None:  # unreachable: the candidate read this same key
                    visited.add(chosen_id)
                    continue
                wait_min = reading[0]
                dur_min = self._get_duration(chosen_id, catalog_index)
                step_min = walk_min + wait_min + dur_min

                if rest_freq is not None:
                    cursor_time, cursor_node, active_minutes_since_rest = (
                        self._maybe_insert_rest(
                            stops,
                            cursor_time,
                            cursor_node,
                            active_minutes_since_rest,
                            rest_freq,
                            step_min,
                            guest_ids,
                            end_time,
                        )
                    )
                    walk_min = self._walk_time(cursor_node, chosen_id)

                arrival = cursor_time + timedelta(minutes=walk_min)
                reading = read_wait(chosen_id, arrival)
                if reading is None:  # unreachable: the candidate read this arrival too
                    visited.add(chosen_id)
                    continue
                wait_min, wait_source = reading
                if isinstance(wait_source, WaitForecast):
                    charged_forecasts.append(wait_source)
                elif wait_source == TYPICAL_WAIT:
                    typical_wait_used = True
                departure = arrival + timedelta(minutes=wait_min + dur_min)
                served = self._compute_served_guests(
                    chosen_id,
                    guest_ids,
                    per_guest_eligible,
                )
                stop = Stop(
                    node_id=chosen_id,
                    kind=StopKind.ATTRACTION,
                    arrival_time=arrival,
                    departure_time=departure,
                    expected_wait_minutes=wait_min,
                    walking_minutes=walk_min,
                    utility=utilities.get(chosen_id, 0.0),
                    served_guests=served,
                )
                stops.append(stop)
                visited.add(chosen_id)
                active_minutes_since_rest += walk_min + wait_min + dur_min
                cursor_time = departure
                cursor_node = chosen_id
                continue

            # 5. If no candidate fits: advance to anchor or lunch if possible
            if anchor_idx < len(anchors):
                curr_anchor = anchors[anchor_idx]
                walk_to_anchor = self._walk_time(cursor_node, curr_anchor.node_id)
                if rest_freq is not None:
                    cursor_time, cursor_node, active_minutes_since_rest = (
                        self._maybe_insert_rest(
                            stops,
                            cursor_time,
                            cursor_node,
                            active_minutes_since_rest,
                            rest_freq,
                            walk_to_anchor + self._show_dur,
                            guest_ids,
                            end_time,
                        )
                    )
                    walk_to_anchor = self._walk_time(cursor_node, curr_anchor.node_id)
                actual_arrival = max(
                    cursor_time + timedelta(minutes=walk_to_anchor),
                    curr_anchor.arrival_time,
                )
                show_departure = actual_arrival + timedelta(minutes=self._show_dur)
                if show_departure <= end_time:
                    anchor_stop = curr_anchor.model_copy(
                        update={
                            "arrival_time": actual_arrival,
                            "departure_time": show_departure,
                            "walking_minutes": walk_to_anchor,
                        }
                    )
                    stops.append(anchor_stop)
                    visited.add(curr_anchor.node_id)
                    cursor_time = show_departure
                    cursor_node = curr_anchor.node_id
                    active_minutes_since_rest += walk_to_anchor + self._show_dur
                anchor_idx += 1
                continue

            if meal_anchor is not None and not meal_inserted:
                lunch_win = constraints.lunch_window
                assert lunch_win is not None
                walk_to_meal = self._walk_time(cursor_node, meal_anchor.node_id)
                actual_arrival = max(
                    cursor_time + timedelta(minutes=walk_to_meal),
                    lunch_win.start,
                )
                if actual_arrival <= lunch_win.end:
                    meal_departure = actual_arrival + timedelta(minutes=self._meal_dur)
                    if meal_departure <= end_time:
                        meal_stop = Stop(
                            node_id=meal_anchor.node_id,
                            kind=StopKind.MEAL,
                            arrival_time=actual_arrival,
                            departure_time=meal_departure,
                            expected_wait_minutes=0.0,
                            walking_minutes=walk_to_meal,
                            utility=0.0,
                            served_guests=guest_ids,
                        )
                        stops.append(meal_stop)
                        cursor_time = meal_departure
                        cursor_node = meal_anchor.node_id
                        active_minutes_since_rest = 0.0
                meal_inserted = True
                continue

            # Nothing else can be inserted
            break

        # Flush any remaining anchors (in case any was left)
        while anchor_idx < len(anchors):
            curr_anchor = anchors[anchor_idx]
            walk_to_anchor = self._walk_time(cursor_node, curr_anchor.node_id)
            actual_arrival = max(
                cursor_time + timedelta(minutes=walk_to_anchor),
                curr_anchor.arrival_time,
            )
            show_departure = actual_arrival + timedelta(minutes=self._show_dur)
            if show_departure <= end_time:
                anchor_stop = curr_anchor.model_copy(
                    update={
                        "arrival_time": actual_arrival,
                        "departure_time": show_departure,
                        "walking_minutes": walk_to_anchor,
                    }
                )
                stops.append(anchor_stop)
                visited.add(curr_anchor.node_id)
                cursor_time = show_departure
                cursor_node = curr_anchor.node_id
            anchor_idx += 1

        # ---- Phase 3: Totals and satisfaction ----------------------------
        total_wait = sum(s.expected_wait_minutes for s in stops)
        total_walk = sum(s.walking_minutes for s in stops)
        objective_value = sum(s.utility for s in stops)
        per_guest_satisfaction = self._calculate_guest_satisfaction(
            stops,
            constraints,
            group_objective,
        )

        scheduled_node_ids = {s.node_id for s in stops}
        unmet_must_do = [
            nid for nid in constraints.must_do if nid not in scheduled_node_ids
        ]

        plan_provenance = provenance or self._minimal_provenance(context)
        if forecast_service is not None:
            plan_provenance = self._with_wait_sources(
                plan_provenance, charged_forecasts, typical_wait_used=typical_wait_used
            )
        if scores is not None:
            plan_provenance = _revalidated(
                plan_provenance, preference_model_version=scores.model_version
            )

        plan = Plan(
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
        if scores is not None:
            # Needs the assembled plan (which stops serve whom), so it comes last.
            plan = _revalidated(plan, per_guest_satisfaction=scorer_satisfaction(plan, scores))
        return plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_start_time(
        self,
        park: Park | None,
        context: LiveContext,
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
            if req.rest_frequency_minutes is not None and req.rest_frequency_minutes > 0
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

    def _wait_at(
        self,
        node_id: str,
        arrival: datetime,
        context: LiveContext,
        catalog_index: dict[str, Attraction],
        forecast_service: ForecastService | None,
        now: datetime | None,
    ) -> tuple[float, _WaitSource] | None:
        """The wait charged at ``node_id`` when the party arrives at ``arrival``.

        Without a forecast service: the posted wait (0 if none), as before.
        With one: the forecast for ``arrival``; if no strategy has a reading,
        the curated ``typical_wait_minutes`` (``TYPICAL_WAIT``); ``None`` if the
        attraction is not in the catalog either, so it is not scheduled. The
        posted wait in ``context`` is not charged directly here: the chain's
        ``CachedSnapshotStrategy`` serves it while the snapshot is fresh (rule
        11's window); a stale posted wait is not trusted, so the typical wait
        is charged instead. A typical wait of 0 is a real value and is charged.
        """
        if forecast_service is None:
            return self._get_wait(node_id, context), _POSTED_WAIT
        assert now is not None  # checked in build_plan
        forecast = forecast_service.forecast_wait(node_id, arrival, now=now)
        if forecast is not None:
            return forecast.wait_minutes, forecast
        attraction = catalog_index.get(node_id)
        if attraction is None:
            return None
        return float(attraction.typical_wait_minutes), TYPICAL_WAIT

    def _get_duration(
        self,
        node_id: str,
        catalog_index: dict[str, Attraction],
    ) -> float:
        """Ride/experience duration estimate (excludes wait time)."""
        if node_id in catalog_index:
            attr = catalog_index[node_id]
            if attr.category == AttractionCategory.SHOW:
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
        node = restaurant_node_ids[0] if restaurant_node_ids else "__restaurant__"
        arrival = window.start
        departure = arrival + timedelta(minutes=self._meal_dur)
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
            gid
            for gid in all_guests
            if gid not in per_guest_eligible or node_id in per_guest_eligible[gid]
        ]

    @staticmethod
    def _calculate_guest_satisfaction(
        stops: list[Stop],
        constraints: PartyConstraints,
        group_objective: GroupObjective | None,
    ) -> dict[str, float]:
        """Normalized per-guest satisfaction [C20], when no ``PreferenceScores`` are given.

        With scores, ``build_plan`` uses the scorer's ``per_guest_satisfaction``
        instead: this count-based version makes a guest with a large eligible
        set look less satisfied than one with a small set, whatever the plan
        gives each of them.

        When ``GroupObjective.per_guest_eligible`` is present, satisfaction
        for each guest is:
            (count of eligible attractions visited where guest was served) /
            (total count of eligible attractions for that guest).
        If eligible set is empty, satisfaction is 1.0.

        Fallback (when ``GroupObjective`` is absent):
            (attractions served to guest) / (total attraction stops in plan).
        """
        guest_ids = [g.guest_id for g in constraints.guests]

        if group_objective and group_objective.per_guest_eligible:
            satisfaction: dict[str, float] = {}
            for gid in guest_ids:
                eligible_count = len(
                    group_objective.per_guest_eligible.get(gid, []),
                )
                if eligible_count == 0:
                    satisfaction[gid] = 1.0  # nothing to satisfy
                else:
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
            sum(1 for s in stops if s.kind == StopKind.ATTRACTION),
            1,
        )
        return {
            gid: sum(
                1
                for s in stops
                if s.kind == StopKind.ATTRACTION and gid in s.served_guests
            )
            / total_stops
            for gid in guest_ids
        }

    @staticmethod
    def _with_wait_sources(
        provenance: Provenance, forecasts: list[WaitForecast], *, typical_wait_used: bool
    ) -> Provenance:
        """Record where the charged waits came from (section 40).

        ``forecast_strategy`` is **replaced**, also on a caller's provenance: it
        names the strategies of the waits actually charged, plus ``typical_wait``
        when a stop fell back to the curated typical wait, and is ``"none"`` when
        the plan charged no queue at all. ``data_sources`` keeps the caller's
        sources and adds the providers behind the forecasts; the typical wait is
        curated reference data, not a provider, so it adds none.
        """
        also = [TYPICAL_WAIT] if typical_wait_used else []
        data_sources = set(provenance.data_sources) | set(forecast_data_sources(forecasts))
        return _revalidated(
            provenance,
            forecast_strategy=forecast_strategy_label(forecasts, also=also),
            data_sources=sorted(data_sources, key=lambda d: d.value),
        )

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
