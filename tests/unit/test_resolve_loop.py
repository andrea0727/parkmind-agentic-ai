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
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.repair_moves import RULE_TO_REPAIR_ACTION, RepairAction
from parkmind.services.planning.resolve_loop import PlannerResolveLoop


# ---------------------------------------------------------------------------
# Helpers — Provenance / Stop / Plan mínimos reales (sin MagicMock)
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
# Mock optimizer / checker
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
    )

    assert result.valid is True
    assert result.plan is empty_plan
    assert optimizer.call_count == 1
    assert checker.call_count == 1


def test_infeasible_constraint_fails_closed(base_constraints, empty_plan):
    """MUST_DO ahora aplica degradacion elegante (§21): retorna valid=True con unmet_must_do."""
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.MUST_DO, message="must do failed")
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
    )

    assert result.valid is True
    assert result.plan is not None
    assert result.fatal_error is None


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
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    assert "space_mountain" in optimizer.last_constraints.avoid


def test_repair_move_forbid_node_missing_target_id(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.OPENING_HOURS, message="closed", stop_id=None)
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
    )

    assert result.valid is False
    assert "FORBID_NODE missing target_id" in result.fatal_error


def test_repair_move_window_reinserts_neighbor():
    """LUNCH_WINDOW: quita el vecino de menor utilidad y reintenta. NO muta PartyConstraints (Punto 3)."""
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
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    # El vecino anterior debe haberse prohibido
    assert "prev_attraction" in optimizer.last_constraints.avoid
    # NUNCA mutar lunch_window (Punto 3)
    assert mock_constraints.lunch_window.start == DAY.replace(hour=11)
    assert mock_constraints.lunch_window.end == DAY.replace(hour=13)


def test_repair_move_walking_budget_removes_optional_stop():
    """WALKING_BUDGET: quita la parada opcional de menor utilidad. NO muta walking_budget (Punto 3)."""
    must_stop = _stop("must_attraction", hour_start=10, utility=5.0)
    opt_stop = _stop("optional_attraction", hour_start=11, utility=1.0)
    plan_first = _plan(stops=[must_stop, opt_stop])
    plan_second = _plan(stops=[must_stop])

    mock_constraints = MagicMock()
    mock_constraints.avoid = []
    mock_constraints.lunch_window = None
    mock_constraints.must_do = ["must_attraction"]
    mock_constraints.party_walking_budget_minutes = 100

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
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    assert "optional_attraction" in optimizer.last_constraints.avoid
    # NUNCA mutar party_walking_budget_minutes (Punto 3)
    assert mock_constraints.party_walking_budget_minutes == 100


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
        context_reloader=mock_reloader,
    )

    assert result.valid is True
    assert reloader_called is True
    # Punto 6: DATA_FRESHNESS no vuelve a llamar al optimizador
    assert optimizer.call_count == 1


def test_data_freshness_reload_fails_closed_if_twice(base_constraints, empty_plan):
    """Punto 6: re-check persiste DATA_FRESHNESS -> falla cerrado, optimizer solo se llama 1 vez."""
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")
            ],
        ),
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="still stale")
            ],
        ),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    def mock_reloader():
        return MagicMock()

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        context_reloader=mock_reloader,
    )

    assert result.valid is False
    assert result.plan is None
    assert "DATA_FRESHNESS violation but no context_reloader provided" in result.fatal_error
    # Punto 6: el optimizador solo es invocado UNA vez
    assert optimizer.call_count == 1


def test_data_freshness_reload_exception_fails_closed(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    def mock_failing_reloader():
        raise ContextReloadError("Failed to fetch fresh snapshot")

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
        context_reloader=mock_failing_reloader,
    )

    assert result.valid is False
    assert result.plan is None
    assert "Context reload failed: Failed to fetch fresh snapshot" in result.fatal_error


def test_unavailable_must_do_yields_valid_plan_with_unmet_must_do(base_constraints):
    """DoD: Un must_do que no pudo insertarse genera valid=True con unmet_must_do poblado."""
    plan_without_must_do = _plan(stops=[_stop("some_other_attraction")])
    base_constraints.must_do = ["splash_mountain"]

    optimizer = MockOptimizer(plan_without_must_do)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.MUST_DO, message="must_do not in plan")
            ],
        )
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
    )

    assert result.valid is True
    assert result.plan is not None
    assert "splash_mountain" in result.unmet_must_do
    assert result.fatal_error is None


def test_repair_move_catalog_covers_all_canonical_rules():
    """Architecture §21: Exactly one deterministic repair move per canonical RuleId."""
    for rule in RuleId:
        assert rule in RULE_TO_REPAIR_ACTION, f"Rule {rule} missing from RULE_TO_REPAIR_ACTION"
        assert isinstance(RULE_TO_REPAIR_ACTION[rule], RepairAction)
