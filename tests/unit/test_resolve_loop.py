"""
Tests for the PlannerReSolveLoop orchestrator.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from parkmind.core.contracts import (
    CheckResult,
    ConstraintViolation,
    RuleId,
)
from parkmind.services.planning.resolve_loop import PlannerResolveLoop


class MockOptimizer:
    def __init__(self, plan_to_return):
        self.plan_to_return = plan_to_return
        self.call_count = 0
        self.last_constraints = None

    def build_plan(self, constraints, **kwargs):
        self.call_count += 1
        self.last_constraints = constraints
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
        now=datetime.now()
    )
    
    assert result.valid is True
    assert result.plan is empty_plan
    assert optimizer.call_count == 1
    assert checker.call_count == 1


def test_infeasible_constraint_fails_closed(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(valid=False, violations=[
            ConstraintViolation(rule=RuleId.MUST_DO, message="must do failed")
        ])
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)
    
    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now()
    )
    
    assert result.valid is False
    assert result.plan is None
    assert "Infeasible constraint set" in result.fatal_error
    assert optimizer.call_count == 1


def test_max_attempts_reached(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(valid=False, violations=[
            ConstraintViolation(rule=RuleId.OPENING_HOURS, message="closed", stop_id="a1")
        ])
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=2)
    
    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now()
    )
    
    assert result.valid is False
    assert result.plan is None
    assert "Failed to find valid plan after 2 attempts" in result.fatal_error
    assert optimizer.call_count == 2
    assert "a1" in optimizer.last_constraints.avoid


def test_data_freshness_reload(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(valid=False, violations=[
            ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")
        ]),
        CheckResult(valid=True, violations=[])
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
        now=datetime.now(),
        context_reloader=mock_reloader
    )
    
    assert result.valid is True
    assert reloader_called is True
    assert optimizer.call_count == 2
    
def test_data_freshness_reload_fails_closed_if_twice(base_constraints, empty_plan):
    optimizer = MockOptimizer(empty_plan)
    checker = MockChecker([
        CheckResult(valid=False, violations=[
            ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")
        ]),
        CheckResult(valid=False, violations=[
            ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="still stale")
        ])
    ])
    loop = PlannerResolveLoop(optimizer, checker, max_attempts=3)
    
    def mock_reloader():
        return MagicMock()
        
    result = loop.resolve(
        constraints=base_constraints,
        context=MagicMock(),
        utilities={},
        now=datetime.now(),
        context_reloader=mock_reloader
    )
    
    assert result.valid is False
    assert result.plan is None
    assert "DATA_FRESHNESS violation but no context_reloader provided" in result.fatal_error
    assert optimizer.call_count == 2
