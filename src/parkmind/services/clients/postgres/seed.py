"""Reproducible development scenario (backlog P0-12 Done-when).

Architecture section 38: same park, same day, same constraints -- only the
profiles differ. A relaxed family and an adult maximizer share Magic Kingdom
on 2026-09-16; the maximizer's plan is ACTIVE and the family's plan is a
PENDING candidate, so both halves of "active vs candidate" are visible.

Deterministic on purpose: fixed ids, fixed park-local times, no ``now()`` and no
randomness. Running it twice, or against two fresh databases, yields identical
content. Catalog metadata comes from the curated ThemeParks reference table, so
no attraction fact is invented here. Every write goes through the repositories,
so the scenario is validated against the section 33 contracts.
"""

from datetime import datetime, timedelta
from typing import Any

import psycopg

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    ApprovalStatus,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    BehaviorEntry,
    BehaviorEventType,
    CoverageReport,
    DataSource,
    Event,
    EventSource,
    EventType,
    Guest,
    GuestProfile,
    GuestRole,
    LiveContext,
    MobilityRequirement,
    Park,
    Plan,
    PlanDiff,
    PlanExecutionState,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    PreferenceValue,
    Proposal,
    Provenance,
    SensitivityKind,
    SensitivityLevel,
    Stop,
    StopKind,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.clients.postgres.attraction_repository import (
    PostgresAttractionRepository,
)
from parkmind.services.clients.postgres.behavior_log_repository import (
    PostgresBehaviorLogRepository,
)
from parkmind.services.clients.postgres.event_repository import PostgresEventRepository
from parkmind.services.clients.postgres.execution_state_repository import (
    PostgresExecutionStateRepository,
)
from parkmind.services.clients.postgres.guest_repository import PostgresGuestRepository
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.clients.postgres.profile_repository import (
    PostgresProfileRepository,
)
from parkmind.services.clients.postgres.proposal_repository import (
    PostgresProposalRepository,
)
from parkmind.services.clients.postgres.session_store import PostgresSessionStore
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)

DEFAULT_AS_OF = datetime(2026, 9, 16, 8, 0, tzinfo=PARK_TZ)
THREAD_ID = "thread_seed"
PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"  # Magic Kingdom (ThemeParks Wiki)
SNAPSHOT_ID = "snap_seed_1"

RELAXED_ADULT = "g_relaxed_adult"
RELAXED_CHILD = "g_relaxed_child"
MAXIMIZER = "g_maximizer"

ACTIVE_PLAN_ID = "plan_seed_maximizer"
CANDIDATE_PLAN_ID = "plan_seed_family"
ACTIVE_PROPOSAL_ID = "prop_seed_active"
CANDIDATE_PROPOSAL_ID = "prop_seed_candidate"

SMALL_WORLD = "f5aad2d4-a419-4384-bd9a-42f86385c750"
JUNGLE_CRUISE = "796b0a25-c51e-456e-9bb8-50a324e301b3"
HAUNTED_MANSION = "2551a77d-023f-4ab1-9a19-8afec0190f39"
TRON = "5a43d1a7-ad53-4d25-abfe-25625f0da304"
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
SEVEN_DWARFS = "9d4d5229-7142-44b6-b4fb-528920969a2c"

# node_id -> display name; category, height, typical wait and outdoor flag come
# from the curated reference table.
_ATTRACTION_NAMES = {
    SMALL_WORLD: '"it\'s a small world"',
    JUNGLE_CRUISE: "Jungle Cruise",
    HAUNTED_MANSION: "Haunted Mansion",
    TRON: "TRON Lightcycle / Run",
    SPACE_MOUNTAIN: "Space Mountain",
    BIG_THUNDER: "Big Thunder Mountain Railroad",
    SEVEN_DWARFS: "Seven Dwarfs Mine Train",
}

_MAXIMIZER_STOPS = [(TRON, 9, 5), (SPACE_MOUNTAIN, 10, 15), (BIG_THUNDER, 11, 20), (SEVEN_DWARFS, 12, 30)]
_FAMILY_STOPS = [(SMALL_WORLD, 10, 10), (JUNGLE_CRUISE, 11, 0), (HAUNTED_MANSION, 12, 0)]


def _catalog() -> list[Attraction]:
    return [
        Attraction(node_id=node_id, name=name, **MAGIC_KINGDOM_ATTRACTION_METADATA[node_id])
        for node_id, name in _ATTRACTION_NAMES.items()
    ]


def _stated(value: float, as_of: datetime) -> PreferenceValue:
    return PreferenceValue(
        value=value,
        source=PreferenceSource.STATED,
        confidence=0.9,
        updated_at=as_of,
        stated_value=value,
    )


def _profiles(as_of: datetime) -> list[GuestProfile]:
    relaxed = GuestProfile(
        guest_id=RELAXED_ADULT,
        pace=PlanningPace.RELAXED,
        queue_tolerance=_stated(0.3, as_of),
        walking_tolerance=_stated(0.4, as_of),
        sensitivities={SensitivityKind.INTENSITY: SensitivityLevel.HIGH},
        preferred_categories=[AttractionCategory.FAMILY, AttractionCategory.DARK_RIDE],
        avoided_categories=[AttractionCategory.THRILL],
        planning_style=PlanningStyle.FLEXIBLE,
        profile_version=1,
    )
    maximizer = GuestProfile(
        guest_id=MAXIMIZER,
        pace=PlanningPace.MAXIMIZER,
        queue_tolerance=_stated(0.9, as_of),
        walking_tolerance=_stated(0.9, as_of),
        preferred_categories=[AttractionCategory.THRILL],
        planning_style=PlanningStyle.STRUCTURED,
        profile_version=1,
    )
    return [relaxed, maximizer]


def _provenance(as_of: datetime) -> Provenance:
    return Provenance(
        snapshot_id=SNAPSHOT_ID,
        retrieved_at=as_of,
        data_sources=[DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO],
        forecast_strategy="seed_fixture",
        optimizer_strategy="seed_fixture",
        constraints_version=1,
        objective_version="seed_v1",
        preference_model_version="seed_v1",
        prompt_versions={},
        weight_provenance={"queue_tolerance": PreferenceSource.STATED},
    )


def _plan(
    plan_id: str,
    stops: list[tuple[str, int, int]],
    guests: list[str],
    as_of: datetime,
) -> Plan:
    day = as_of.astimezone(PARK_TZ)
    built = []
    for node_id, hour, minute in stops:
        arrival = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        built.append(
            Stop(
                node_id=node_id,
                kind=StopKind.ATTRACTION,
                arrival_time=arrival,
                departure_time=arrival + timedelta(minutes=40),
                expected_wait_minutes=30.0,
                walking_minutes=5.0,
                utility=1.0,
                served_guests=guests,
            )
        )
    return Plan(
        plan_id=plan_id,
        version=1,
        stops=built,
        total_wait_minutes=30.0 * len(built),
        total_walking_minutes=5.0 * len(built),
        objective_value=float(len(built)),
        per_guest_satisfaction=dict.fromkeys(guests, 0.8),
        provenance=_provenance(as_of),
    )


def _live_context(as_of: datetime) -> tuple[LiveContext, dict[str, Any]]:
    waits = {
        node_id: WaitEstimate(
            attraction_id=node_id,
            wait_minutes=float(MAGIC_KINGDOM_ATTRACTION_METADATA[node_id]["typical_wait_minutes"]),
            status=AttractionStatus.OPERATING,
        )
        for node_id in _ATTRACTION_NAMES
    }
    context = LiveContext(
        snapshot_id=SNAPSHOT_ID,
        retrieved_at=as_of,
        waits=waits,
        statuses=dict.fromkeys(_ATTRACTION_NAMES, AttractionStatus.OPERATING),
        weather=[
            WeatherHour(
                timestamp=as_of.replace(hour=hour),
                condition="clear",
                temperature_f=84.0,
                precipitation_probability=0.1,
            )
            for hour in (9, 10, 11, 12)
        ],
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    )
    raw = {
        "source": "seed_fixture",
        "liveData": [
            {
                "id": node_id,
                "status": "OPERATING",
                "queue": {"STANDBY": {"waitTime": int(wait.wait_minutes)}},
            }
            for node_id, wait in waits.items()
        ],
    }
    return context, raw


def seed_dev_scenario(
    conn: psycopg.Connection[Any], *, as_of: datetime = DEFAULT_AS_OF
) -> None:
    """Load the scenario; safe to run repeatedly and against a fresh database.

    ``as_of`` fixes the scenario's day and every bookkeeping timestamp.
    """
    guests = PostgresGuestRepository(conn)
    profiles = PostgresProfileRepository(conn)
    behavior = PostgresBehaviorLogRepository(conn)
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)

    # -- park data ---------------------------------------------------------------
    attractions = PostgresAttractionRepository(conn)
    attractions.save_catalog(PARK_ID, _catalog())
    day = as_of.astimezone(PARK_TZ).replace(second=0, microsecond=0)
    attractions.save_schedule(
        Park(
            park_id=PARK_ID,
            name="Magic Kingdom Park",
            opening_time=day.replace(hour=9, minute=0),
            closing_time=day.replace(hour=22, minute=0),
            outdoor=True,
        )
    )
    context, raw = _live_context(as_of)
    PostgresSnapshotRepository(conn).save(
        context, raw, [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO]
    )

    # -- guests, profiles, accessibility -------------------------------------------
    for guest in (
        Guest(guest_id=RELAXED_ADULT, role=GuestRole.ADULT, height_cm=170.0),
        Guest(guest_id=RELAXED_CHILD, role=GuestRole.CHILD, height_cm=110.0),
        Guest(guest_id=MAXIMIZER, role=GuestRole.ADULT, height_cm=182.0),
    ):
        guests.save(guest)
    for profile in _profiles(as_of):
        profiles.save(profile)
    # Persisted only because consent is explicit; derived flags, never a statement.
    PostgresSessionStore(conn).put(
        THREAD_ID,
        AccessibilityRequirements(
            guest_id=RELAXED_ADULT,
            daily_walking_limit_minutes=90,
            mobility_requirements=[MobilityRequirement.LIMITED_WALKING],
            consent=True,
            retention_policy="persisted",
        ),
    )

    # -- an ACTIVE plan (approved) and a PENDING candidate --------------------------
    active = _plan(ACTIVE_PLAN_ID, _MAXIMIZER_STOPS, [MAXIMIZER], as_of)
    plans.save(THREAD_ID, active)
    existing = proposals.get(ACTIVE_PROPOSAL_ID)
    if existing is None:
        proposals.save(
            THREAD_ID,
            Proposal(
                proposal_id=ACTIVE_PROPOSAL_ID,
                base_plan_id=ACTIVE_PLAN_ID,
                candidate_plan_id=ACTIVE_PLAN_ID,
                reason="seed: initial plan for the maximizer",
                diff=PlanDiff(stops_added=[s.node_id for s in active.stops]),
                explanation="Seed fixture: four thrill attractions in a row.",
                approval_status=ApprovalStatus.PENDING,
                provenance=_provenance(as_of),
            ),
        )
    if existing is None or existing.approval_status is ApprovalStatus.PENDING:
        proposals.resolve(ACTIVE_PROPOSAL_ID, ApprovalStatus.APPROVED, at=as_of)
    plans.activate(THREAD_ID, ACTIVE_PLAN_ID, at=as_of)

    candidate = _plan(CANDIDATE_PLAN_ID, _FAMILY_STOPS, [RELAXED_ADULT, RELAXED_CHILD], as_of)
    plans.save(THREAD_ID, candidate)
    proposals.save(
        THREAD_ID,
        Proposal(
            proposal_id=CANDIDATE_PROPOSAL_ID,
            base_plan_id=ACTIVE_PLAN_ID,
            candidate_plan_id=CANDIDATE_PLAN_ID,
            reason="seed: gentler alternative for the relaxed family",
            diff=PlanDiff(
                stops_added=[s.node_id for s in candidate.stops],
                stops_removed=[s.node_id for s in active.stops],
            ),
            explanation="Seed fixture: low-intensity rides only.",
            approval_status=ApprovalStatus.PENDING,
            provenance=_provenance(as_of),
        ),
    )

    # -- progress, behavior, events --------------------------------------------------
    PostgresExecutionStateRepository(conn).save(
        PlanExecutionState(
            plan_id=ACTIVE_PLAN_ID,
            completed_stop_ids=[TRON],
            current_location_node_id=TRON,
            as_of=as_of + timedelta(hours=1, minutes=30),
            walking_minutes_consumed={MAXIMIZER: 5.0},
        )
    )
    behavior.append(
        MAXIMIZER,
        BehaviorEntry(
            entry_id="seed_entry_1",
            event_type=BehaviorEventType.PROPOSAL_ACCEPTED,
            proposal_id=ACTIVE_PROPOSAL_ID,
            plan_id=ACTIVE_PLAN_ID,
            decision="ACCEPTED",
            attraction_ids=[s.node_id for s in active.stops],
            timestamp=as_of + timedelta(minutes=1),
        ),
    )
    # Left unprocessed on purpose: severity / requires_replan are EventPolicy's to set.
    PostgresEventRepository(conn).record(
        THREAD_ID,
        Event(
            event_id="seed_event_1",
            type=EventType.ATTRACTION_DOWN,
            source=EventSource.MONITOR,
            attraction_id=SPACE_MOUNTAIN,
            confidence=1.0,
            timestamp=as_of + timedelta(hours=2),
        ),
    )
