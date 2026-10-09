"""
Tests for the PlannerReSolveLoop orchestrator.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    CheckResult,
    ConstraintViolation,
    Provenance,
    RuleId,
    StopKind,
    TimeWindow,
)
from parkmind.core.contracts.models import Plan, Stop
from parkmind.services.planning.repair_moves import RULE_TO_REPAIR_ACTION, RepairAction
from parkmind.services.planning.resolve_loop import PlannerResolveLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DAY = datetime(2026, 10, 8, tzinfo=PARK_TZ)


def _provenance() -> Provenance:
    return Provenance(
        snapshot_id="snap-test",
        retrieved_at=DAY.replace(hour=9),
        forecast_strategy="typical_wait",
        optimizer_strategy="greedy_insertion",
        constraints_version=1,
        objective_version="1",
        preference_model_version="auto",
    )


def _stop(
    node_id: str,
    kind: StopKind = StopKind.ATTRACTION,
    hour_start: int = 10,
    duration_min: int = 30,
    utility: float = 3.0,
) -> Stop:
    t0 = DAY.replace(hour=hour_start, minute=0, second=0, microsecond=0)
    t1 = t0 + timedelta(minutes=duration_min)
    return Stop(
        node_id=node_id,
        kind=kind,
        arrival_time=t0,
        departure_time=t1,
        expected_wait_minutes=0.0,
        walking_minutes=5.0,
        utility=utility,
    )


def _plan(
    stops: list | None = None,
    unmet_must_do: list | None = None,
) -> Plan:
    return Plan(
        plan_id="plan-test",
        version=1,
        stops=stops or [],
        total_wait_minutes=0.0,
        total_walking_minutes=0.0,
        objective_value=0.0,
        unmet_must_do=unmet_must_do or [],
        provenance=_provenance(),
    )


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

class MockOptimizer:
    def __init__(self, plan_to_return=None, plan_factory=None):
        self.plan_to_return = plan_to_return
        self.plan_factory = plan_factory
        self.call_count = 0
        self.last_constraints = None

    def build_plan(self, constraints, **kwargs):
        self.call_count += 1
        self.last_constraints = constraints
        if self.plan_factory:
            return self.plan_factory(self.call_count, constraints)
        return self.plan_to_return


class MockChecker:
    def __init__(self, results_to_return):
        self.results_to_return = results_to_return
        self.call_count = 0

    def check(self, **kwargs):
        if self.call_count < len(self.results_to_return):
            res = self.results_to_return[self.call_count]
        else:
            res = self.results_to_return[-1]
        self.call_count += 1
        return res


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_constraints():
    mock = MagicMock()
    mock.avoid = []
    mock.lunch_window = None
    mock.party_walking_budget_minutes = 100
    mock.must_do = []
    return mock


@pytest.fixture
def empty_plan() -> Plan:
    return _plan()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_first_attempt(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([CheckResult(valid=True, violations=[])])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is True
    assert result.plan is empty_plan
    assert optimizer.call_count == 1
    assert checker.call_count == 1


def test_infeasible_physical_constraints_fail_closed(base_constraints, empty_plan):
    for rule in [RuleId.HEIGHT, RuleId.RIDE_RESTRICTION]:
        optimizer = MockOptimizer(empty_plan)
        checker = MockChecker([
            CheckResult(
                valid=False,
                violations=[
                    ConstraintViolation(rule=rule, message="safety rule violation")
                ],
            )
        ])
        loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

        result = loop.resolve(
            constraints=base_constraints,
            context=MagicMock(),
            utilities={},
            now=datetime.now(tz=PARK_TZ),
            park=MagicMock(),
        )

        assert result.valid is False
        assert result.plan is None
        assert "Infeasible constraint set" in result.fatal_error
        assert optimizer.call_count == 1


def test_max_attempts_reached(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.OPENING_HOURS, message="closed", stop_id="a1")
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=2)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is False
    assert result.plan is None
    assert "Failed to find valid plan after 2 attempts" in result.fatal_error
    assert optimizer.call_count == 2
    assert "a1" in optimizer.last_constraints.avoid


def test_repair_move_forbid_node(base_constraints):
    """OPENING_HOURS: agrega el nodo a avoid y reintenta hasta converger."""
    plan_with_stop = _plan(stops=[_stop("space_mountain")])
    plan_clean = _plan()

    optimizer = MockOptimizer(
        plan_factory=lambda n, _c: plan_with_stop if n == 1 else plan_clean
    )
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(
                    rule=RuleId.OPENING_HOURS,
                    message="closed",
                    stop_id="space_mountain",
                )
            ],
        ),
        CheckResult(valid=True, violations=[]),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    assert "space_mountain" in optimizer.last_constraints.avoid


def test_repair_move_window_reinserts_neighbor():
    """LUNCH_WINDOW: quita el vecino de menor utilidad y reintenta."""
    prev_stop = _stop("prev_attraction", hour_start=10, utility=1.0)
    window_stop = _stop("restaurant_1", kind=StopKind.MEAL, hour_start=11, utility=0.0)
    plan_first = _plan(stops=[prev_stop, window_stop])
    plan_second = _plan(stops=[window_stop])

    mock_constraints = MagicMock()
    mock_constraints.avoid = []
    mock_constraints.lunch_window = TimeWindow(
        start=DAY.replace(hour=11), end=DAY.replace(hour=13)
    )
    mock_constraints.must_do = []
    mock_constraints.party_walking_budget_minutes = 100

    optimizer = MockOptimizer(
        plan_factory=lambda n, _c: plan_first if n == 1 else plan_second
    )
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(
                    rule=RuleId.LUNCH_WINDOW,
                    message="missed lunch",
                    stop_id="restaurant_1",
                )
            ],
        ),
        CheckResult(valid=True, violations=[]),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=mock_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    assert "prev_attraction" in optimizer.last_constraints.avoid


def test_repair_move_walking_budget_removes_optional_stop():
    """WALKING_BUDGET: quita la parada opcional de menor utilidad."""
    must_stop = _stop("must_attraction", hour_start=10, utility=5.0)
    opt_stop = _stop("optional_attraction", hour_start=11, utility=1.0)
    plan_first = _plan(stops=[must_stop, opt_stop])
    plan_second = _plan(stops=[must_stop])

    mock_constraints = MagicMock()
    mock_constraints.avoid = []
    mock_constraints.must_do = ["must_attraction"]

    optimizer = MockOptimizer(
        plan_factory=lambda n, _c: plan_first if n == 1 else plan_second
    )
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(
                    rule=RuleId.WALKING_BUDGET, message="walking budget exceeded"
                )
            ],
        ),
        CheckResult(valid=True, violations=[]),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=mock_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    assert "optional_attraction" in optimizer.last_constraints.avoid


def test_data_freshness_reload(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")
            ],
        ),
        CheckResult(valid=True, violations=[]),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    reloader_called = False
    new_context = MagicMock()

    def mock_reloader():
        nonlocal reloader_called
        reloader_called = True
        return new_context

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
        context_reloader=mock_reloader,
    )

    assert result.valid is True
    assert reloader_called is True
    assert optimizer.call_count == 1


# ---------------------------------------------------------------------------
# PROBES (Architecture §21 Invariants)
# ---------------------------------------------------------------------------

def test_must_do_operating_fails_closed(base_constraints, empty_plan):
    """Probe 1: A MUST_DO operating that cannot be inserted fails closed instead of returning valid=True."""
    base_constraints.must_do = ["splash_mountain"]
    
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.MUST_DO, message="must_do slot not available", stop_id="splash_mountain")
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is False
    assert result.plan is None
    assert "MUST_DO attraction 'splash_mountain' is OPERATING" in result.fatal_error


def test_opening_hours_protects_must_do(base_constraints):
    """Probe 3: FORBID_NODE should fail closed rather than removing a MUST_DO node."""
    base_constraints.must_do = ["space_mountain"]
    plan_with_stop = _plan(stops=[_stop("space_mountain")])

    optimizer = MockOptimizer(plan_with_stop)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(
                    rule=RuleId.OPENING_HOURS,
                    message="closed",
                    stop_id="space_mountain",
                )
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        park=MagicMock(),
    )

    assert result.valid is False
    assert "cannot forbid MUST_DO node 'space_mountain'" in result.fatal_error


def test_repair_move_catalog_covers_all_canonical_rules():
    """Architecture §21: Exactly one deterministic repair move per canonical RuleId."""
    for rule in RuleId:
        assert rule in RULE_TO_REPAIR_ACTION, f"Rule {rule} missing from RULE_TO_REPAIR_ACTION"
        assert isinstance(RULE_TO_REPAIR_ACTION[rule], RepairAction)