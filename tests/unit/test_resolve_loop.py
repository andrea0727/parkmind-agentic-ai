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
    RuleId,
    TimeWindow,
)
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.repair_moves import RULE_TO_REPAIR_ACTION, RepairAction
from parkmind.services.planning.resolve_loop import PlannerResolveLoop


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


@pytest.fixture
def base_constraints():
    mock = MagicMock()
    mock.avoid = []
    mock.lunch_window = None
    mock.party_walking_budget_minutes = 100
    mock.must_do = []
    return mock


@pytest.fixture
def empty_plan():
    mock = MagicMock()
    mock.unmet_must_do = []
    return mock


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

    assert result.valid is False
    assert result.plan is None
    assert "Infeasible constraint set" in result.fatal_error
    assert optimizer.call_count == 1


def test_infeasible_physical_constraints_fail_closed(base_constraints, empty_plan):
    for rule in [RuleId.HEIGHT, RuleId.ACCESSIBILITY, RuleId.RIDE_RESTRICTION, RuleId.DEPARTURE]:
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


def test_repair_move_forbid_node(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.OPENING_HOURS, message="closed", stop_id="space_mountain")
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


def test_repair_move_relax_lunch_window(empty_plan):
    now = datetime(2026, 10, 7, 10, 0, tzinfo=PARK_TZ)
    start = datetime(2026, 10, 7, 12, 0, tzinfo=PARK_TZ)
    end = datetime(2026, 10, 7, 13, 0, tzinfo=PARK_TZ)
    initial_lunch = TimeWindow(start=start, end=end)

    mock_constraints = MagicMock()
    mock_constraints.avoid = []
    mock_constraints.lunch_window = initial_lunch
    mock_constraints.party_walking_budget_minutes = 100

    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.LUNCH_WINDOW, message="missed lunch")
            ],
        ),
        CheckResult(valid=True, violations=[]),
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=mock_constraints,
        context=MagicMock(),
        utilities={},
        now=now,
    )

    assert result.valid is True
    assert optimizer.call_count == 2
    updated_lunch = optimizer.last_constraints.lunch_window
    assert updated_lunch.start == start - timedelta(minutes=15)
    assert updated_lunch.end == end + timedelta(minutes=15)


def test_repair_move_relax_walking_budget(empty_plan):
    mock_constraints = MagicMock()
    mock_constraints.avoid = []
    mock_constraints.lunch_window = None
    mock_constraints.party_walking_budget_minutes = 100

    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(
            valid=False,
            violations=[
                ConstraintViolation(rule=RuleId.WALKING_BUDGET, message="walking budget exceeded")
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
    assert optimizer.last_constraints.party_walking_budget_minutes == 120


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
    assert optimizer.call_count == 2


def test_data_freshness_reload_fails_closed_if_twice(base_constraints, empty_plan):
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
    assert optimizer.call_count == 2


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
    """
    DoD requirement: An unavailable must-do produces a valid plan
    with unmet_must_do populated (test).
    """
    mock_plan = MagicMock()
    mock_plan.unmet_must_do = ["splash_mountain"]

    base_constraints.must_do = ["splash_mountain", "space_mountain"]

    optimizer = MockOptimizer(mock_plan)
    checker = MockChecker([CheckResult(valid=True, violations=[])])

    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)

    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(tz=PARK_TZ),
    )

    assert result.valid is True
    assert result.plan is mock_plan
    assert result.unmet_must_do == ["splash_mountain"]
    assert result.fatal_error is None


def test_repair_move_catalog_covers_all_canonical_rules():
    """
    Architecture §21: Exactly one deterministic repair move per canonical RuleId.
    """
    for rule in RuleId:
        assert rule in RULE_TO_REPAIR_ACTION, f"Rule {rule} missing from RULE_TO_REPAIR_ACTION"
        assert isinstance(RULE_TO_REPAIR_ACTION[rule], RepairAction)
