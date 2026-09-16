"""
Validation tests for ParkMind domain contracts.

Tests both valid and invalid cases per model to ensure:
1. Valid contracts pass validation
2. Invalid contracts fail with clear errors
3. Key invariants are enforced

§5.6 Build order — Validation tests
"""

from datetime import datetime, timedelta
import pytest
from zoneinfo import ZoneInfo

from src.parkmind.contracts import (
    # Enums
    AttractionStatus,
    StopKind,
    SensitivityKind,
    SensitivityLevel,
    AttractionCategory,
    MobilityRequirement,
    RideRestriction,
    EventSource,
    BehaviorEventType,
    RejectionReason,
    EventSeverity,
    ApprovalStatus,
    PreferenceSource,
    GuestRole,
    PlanningPace,
    PlanningStyle,
    DataSource,
    # Base types
    TimeWindow,
    PARK_TZ,
    # Models
    Guest,
    PreferenceValue,
    GuestProfile,
    AccessibilityRequirements,
    PartyConstraints,
    Stop,
    Plan,
    PlanExecutionState,
    Event,
    Proposal,
    Provenance,
    BehaviorEntry,
    BehaviorLog,
    GroupObjective,
    FairnessConfig,
    LiveContext,
    CoverageReport,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def aware_datetime():
    """Timezone-aware datetime in park timezone."""
    return datetime(2026, 9, 15, 10, 0, 0, tzinfo=PARK_TZ)


@pytest.fixture
def naive_datetime():
    """Naive datetime (should fail validation)."""
    return datetime(2026, 9, 15, 10, 0, 0)


@pytest.fixture
def guest_adult():
    """Valid adult guest."""
    return Guest(
        guest_id="g1",
        role="adult",
        height_cm=175.0,
    )


@pytest.fixture
def guest_child():
    """Valid child guest."""
    return Guest(
        guest_id="g2",
        role="child",
        height_cm=120.0,
    )


@pytest.fixture
def preference_stated(aware_datetime):
    """Valid stated preference."""
    return PreferenceValue(
        value=0.8,
        source=PreferenceSource.STATED,
        confidence=0.9,
        updated_at=aware_datetime,
        stated_value=0.8,
    )


@pytest.fixture
def preference_learned(aware_datetime):
    """Valid learned preference."""
    return PreferenceValue(
        value=0.6,
        source=PreferenceSource.LEARNED,
        confidence=0.5,
        updated_at=aware_datetime,
        stated_value=0.8,  # Retained from original statement
    )


# ============================================================================
# TIMEWINDOW TESTS
# ============================================================================


class TestTimeWindow:
    """TimeWindow validation (Invariant #6: timezone-aware)."""

    def test_valid_window_aware(self, aware_datetime):
        """Valid: aware datetimes, end > start."""
        end = aware_datetime + timedelta(hours=1)
        window = TimeWindow(start=aware_datetime, end=end)
        assert window.start.tzinfo is not None
        assert window.end.tzinfo is not None

    def test_invalid_window_naive_start(self, naive_datetime):
        """Invalid: naive start datetime."""
        end = naive_datetime + timedelta(hours=1)
        with pytest.raises(ValueError, match="timezone-aware"):
            TimeWindow(start=naive_datetime, end=end)

    def test_invalid_window_end_before_start(self, aware_datetime):
        """Invalid: end <= start."""
        start = aware_datetime
        end = aware_datetime - timedelta(hours=1)
        with pytest.raises(ValueError, match="end must be after start"):
            TimeWindow(start=start, end=end)


# ============================================================================
# GUEST TESTS
# ============================================================================


class TestGuest:
    """Guest identity validation."""

    def test_valid_adult(self):
        """Valid: adult with height."""
        guest = Guest(guest_id="g1", role="adult", height_cm=180.0)
        assert guest.role == "adult"

    def test_valid_child_no_height(self):
        """Valid: child without height."""
        guest = Guest(guest_id="g2", role="child", height_cm=None)
        assert guest.height_cm is None

    def test_invalid_height_too_low(self):
        """Invalid: height < 50 cm."""
        with pytest.raises(ValueError):
            Guest(guest_id="g1", role="child", height_cm=40.0)

    def test_invalid_height_too_high(self):
        """Invalid: height > 250 cm."""
        with pytest.raises(ValueError):
            Guest(guest_id="g1", role="adult", height_cm=260.0)


# ============================================================================
# PREFERENCE VALUE TESTS
# ============================================================================


class TestPreferenceValue:
    """PreferenceValue validation (Invariant #3: stated_value immutability)."""

    def test_valid_stated(self, aware_datetime):
        """Valid: stated preference with stated_value."""
        pref = PreferenceValue(
            value=0.8,
            source=PreferenceSource.STATED,
            confidence=0.95,
            updated_at=aware_datetime,
            stated_value=0.8,
        )
        assert pref.value == pref.stated_value

    def test_valid_learned_retains_stated(self, aware_datetime):
        """Valid: learned preference retains original stated_value."""
        pref = PreferenceValue(
            value=0.6,  # Learned value changed
            source=PreferenceSource.LEARNED,
            confidence=0.5,
            updated_at=aware_datetime,
            stated_value=0.8,  # Original kept
        )
        assert pref.stated_value == 0.8  # Retained; never overwritten
        assert pref.value == 0.6  # But current value differs

    def test_invalid_stated_without_stated_value(self, aware_datetime):
        """Invalid: stated source requires stated_value."""
        with pytest.raises(ValueError, match="stated_value must be provided"):
            PreferenceValue(
                value=0.8,
                source=PreferenceSource.STATED,
                confidence=0.9,
                updated_at=aware_datetime,
                stated_value=None,  # Missing
            )

    def test_invalid_confidence_out_of_bounds(self, aware_datetime):
        """Invalid: confidence must be 0–1."""
        with pytest.raises(ValueError):
            PreferenceValue(
                value=0.8,
                source="stated",
                confidence=1.5,  # Out of bounds
                updated_at=aware_datetime,
                stated_value=0.8,
            )


# ============================================================================
# GUEST PROFILE TESTS
# ============================================================================


class TestGuestProfile:
    """GuestProfile validation (soft preferences, separate from accessibility)."""

    def test_valid_profile(self, aware_datetime):
        """Valid: complete profile."""
        pref = PreferenceValue(
            value=0.7,
            source=PreferenceSource.STATED,
            confidence=0.8,
            updated_at=aware_datetime,
            stated_value=0.7,
        )
        profile = GuestProfile(
            guest_id="g1",
            pace=PlanningPace.BALANCED,
            queue_tolerance=pref,
            walking_tolerance=pref,
            sensitivities={SensitivityKind.INTENSITY: SensitivityLevel.LOW},
            planning_style=PlanningStyle.STRUCTURED,
            profile_version=1,
        )
        assert profile.queue_tolerance.value == 0.7

    def test_invalid_tolerance_value_out_of_bounds(self, aware_datetime):
        """Invalid: tolerance value must be 0–1."""
        pref = PreferenceValue(
            value=1.5,  # Out of bounds
            source=PreferenceSource.STATED,
            confidence=0.8,
            updated_at=aware_datetime,
            stated_value=1.5,
        )
        with pytest.raises(ValueError, match="Tolerance value must be in range 0–1"):
            GuestProfile(
                guest_id="g1",
                pace=PlanningPace.BALANCED,
                queue_tolerance=pref,
                walking_tolerance=pref,
                planning_style=PlanningStyle.STRUCTURED,
                profile_version=1,
            )


# ============================================================================
# ACCESSIBILITY REQUIREMENTS TESTS
# ============================================================================


class TestAccessibilityRequirements:
    """AccessibilityRequirements validation (Invariant #2: hard constraints)."""

    def test_valid_accessibility_with_consent(self):
        """Valid: hard constraints with explicit consent."""
        acc = AccessibilityRequirements(
            guest_id="g2",
            daily_walking_limit_minutes=90,
            mobility_requirements=[MobilityRequirement.LIMITED_WALKING],
            consent=True,
            retention_policy="session_only",
        )
        assert acc.consent is True

    def test_invalid_accessibility_without_consent(self):
        """Invalid: hard constraints require explicit consent."""
        with pytest.raises(ValueError, match="consent=True required"):
            AccessibilityRequirements(
                guest_id="g2",
                daily_walking_limit_minutes=90,
                consent=False,  # Missing consent
                retention_policy="session_only",
            )

    def test_valid_accessibility_no_constraints(self):
        """Valid: no constraints, no consent needed."""
        acc = AccessibilityRequirements(
            guest_id="g1",
            consent=False,
            retention_policy="session_only",
        )
        assert acc.daily_walking_limit_minutes is None


# ============================================================================
# STOP & PLAN TESTS
# ============================================================================


class TestStop:
    """Stop validation (execution times must be valid)."""

    def test_valid_stop(self, aware_datetime):
        """Valid: complete stop."""
        stop = Stop(
            node_id="space_mountain",
            kind=StopKind.ATTRACTION,
            arrival_time=aware_datetime,
            departure_time=aware_datetime + timedelta(minutes=30),
            expected_wait_minutes=25.0,
            walking_minutes=4.0,
            utility=10.5,
            served_guests=["g1", "g2"],
        )
        assert stop.kind == StopKind.ATTRACTION

    def test_invalid_stop_departure_before_arrival(self, aware_datetime):
        """Invalid: departure before arrival."""
        with pytest.raises(ValueError, match="departure_time must be after arrival_time"):
            Stop(
                node_id="space_mountain",
                kind=StopKind.ATTRACTION,
                arrival_time=aware_datetime,
                departure_time=aware_datetime - timedelta(minutes=10),
                expected_wait_minutes=25.0,
                walking_minutes=4.0,
                utility=10.5,
            )


class TestPlan:
    """Plan validation (must-do, satisfaction, unmet tracking)."""

    def test_valid_plan(self, aware_datetime):
        """Valid: complete plan with stops."""
        stop = Stop(
            node_id="space_mountain",
            kind=StopKind.ATTRACTION,
            arrival_time=aware_datetime,
            departure_time=aware_datetime + timedelta(minutes=30),
            expected_wait_minutes=25.0,
            walking_minutes=4.0,
            utility=10.5,
            served_guests=["g1", "g2"],
        )
        provenance = Provenance(
            snapshot_id="snap_1",
            retrieved_at=aware_datetime,
            forecast_strategy="api",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="v1",
            preference_model_version="default",
        )
        plan = Plan(
            plan_id="plan_1",
            version=1,
            stops=[stop],
            total_wait_minutes=25.0,
            total_walking_minutes=4.0,
            objective_value=10.5,
            per_guest_satisfaction={"g1": 0.9, "g2": 0.85},
            provenance=provenance,
        )
        assert len(plan.stops) == 1

    def test_valid_plan_with_unmet_must_do(self, aware_datetime):
        """Valid: plan with unmet must-dos (graceful degradation C18)."""
        provenance = Provenance(
            snapshot_id="snap_1",
            retrieved_at=aware_datetime,
            forecast_strategy="api",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="v1",
            preference_model_version="default",
        )
        plan = Plan(
            plan_id="plan_1",
            version=1,
            stops=[],
            total_wait_minutes=0.0,
            total_walking_minutes=0.0,
            objective_value=0.0,
            unmet_must_do=["TRON", "Space Mountain"],  # Unavailable
            provenance=provenance,
        )
        assert len(plan.unmet_must_do) == 2


# ============================================================================
# EVENT TESTS
# ============================================================================


class TestEvent:
    """Event validation (source typing, field constraints)."""

    def test_valid_event_monitor(self, aware_datetime):
        """Valid: monitor event with attraction."""
        event = Event(
            event_id="evt_1",
            type="ATTRACTION_DOWN",
            source=EventSource.MONITOR,
            attraction_id="tron",
            confidence=0.95,
            timestamp=aware_datetime,
        )
        assert event.source == EventSource.MONITOR

    def test_valid_event_user_report(self, aware_datetime):
        """Valid: user-reported event with guest."""
        event = Event(
            event_id="evt_2",
            type="GUEST_FATIGUE",
            source=EventSource.USER,
            guest_id="g2",
            confidence=0.88,
            timestamp=aware_datetime,
        )
        assert event.source == EventSource.USER

    def test_invalid_event_missing_attraction(self, aware_datetime):
        """Invalid: ATTRACTION_DOWN requires attraction_id."""
        with pytest.raises(ValueError, match="ATTRACTION_DOWN requires attraction_id"):
            Event(
                event_id="evt_1",
                type="ATTRACTION_DOWN",
                source="monitor",
                # Missing attraction_id
                confidence=0.95,
                timestamp=aware_datetime,
            )

    def test_invalid_event_missing_guest(self, aware_datetime):
        """Invalid: GUEST_FATIGUE requires guest_id."""
        with pytest.raises(ValueError, match="GUEST_FATIGUE requires guest_id"):
            Event(
                event_id="evt_2",
                type="GUEST_FATIGUE",
                source="user",
                # Missing guest_id
                confidence=0.88,
                timestamp=aware_datetime,
            )


# ============================================================================
# BEHAVIOR LOG TESTS
# ============================================================================


class TestBehaviorLog:
    """BehaviorLog validation (separate aggregate C12)."""

    def test_valid_behavior_log(self, aware_datetime):
        """Valid: empty behavior log."""
        log = BehaviorLog(guest_id="g1", entries=[])
        assert len(log.entries) == 0

    def test_valid_behavior_log_with_entries(self, aware_datetime):
        """Valid: log with acceptance entry."""
        entry = BehaviorEntry(
            entry_id="be_1",
            event_type=BehaviorEventType.PROPOSAL_ACCEPTED,
            proposal_id="prop_1",
            decision="ACCEPTED",
            timestamp=aware_datetime,
        )
        log = BehaviorLog(guest_id="g1", entries=[entry])
        assert len(log.entries) == 1
        assert log.entries[0].event_type == BehaviorEventType.PROPOSAL_ACCEPTED


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestInvariantEnforcement:
    """Test key invariants across multiple models."""

    def test_invariant_3_stated_value_retention(self, aware_datetime):
        """Invariant #3: stated_value never overwritten by learning."""
        # Original stated value
        original = PreferenceValue(
            value=0.8,
            source=PreferenceSource.STATED,
            confidence=0.95,
            updated_at=aware_datetime,
            stated_value=0.8,
        )

        # After learning, stated_value is retained
        updated = PreferenceValue(
            value=0.5,  # Changed by learning
            source=PreferenceSource.LEARNED,
            confidence=0.6,
            updated_at=aware_datetime + timedelta(days=1),
            stated_value=0.8,  # RETAINED; never overwritten
        )

        assert updated.stated_value == original.stated_value
        assert updated.value != original.value

    def test_invariant_6_timezone_awareness(self, aware_datetime):
        """Invariant #6: all datetimes timezone-aware."""
        window = TimeWindow(
            start=aware_datetime,
            end=aware_datetime + timedelta(hours=1),
        )
        assert window.start.tzinfo is not None
        assert window.end.tzinfo is not None

    def test_invariant_2_accessibility_separate(self):
        """Invariant #2: accessibility is separate, hard constraint."""
        # Accessibility is NOT in GuestProfile; it's a separate aggregate
        profile = GuestProfile(
            guest_id="g1",
            pace=PlanningPace.BALANCED,
            queue_tolerance=PreferenceValue(
                value=0.7,
                source=PreferenceSource.DEFAULT,
                confidence=0.5,
                updated_at=datetime.now(tz=PARK_TZ),
            ),
            walking_tolerance=PreferenceValue(
                value=0.7,
                source=PreferenceSource.DEFAULT,
                confidence=0.5,
                updated_at=datetime.now(tz=PARK_TZ),
            ),
            planning_style=PlanningStyle.STRUCTURED,
            profile_version=1,
        )

        # Accessibility is passed separately
        accessibility = AccessibilityRequirements(
            guest_id="g1",
            daily_walking_limit_minutes=90,
            consent=True,
            retention_policy="session_only",
        )

        # They are independent aggregates
        assert not hasattr(profile, "accessibility_requirements")
        assert accessibility.daily_walking_limit_minutes == 90
