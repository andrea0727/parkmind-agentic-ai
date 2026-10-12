"""What leaves the system through ``planner.*`` [C19]: no accessibility requirement
in a violation or in a re-solve failure, for every private rule; the other rules
pass through untouched."""

from typing import Any

import factories
import pytest

from parkmind.core.contracts import CheckResult, ConstraintViolation, RuleId
from parkmind.services.planning.resolve_loop import PlannerResolveLoop
from parkmind.services.use_cases.planner_queries import (
    public_check,
    public_failure,
    public_violation,
)

PRIVATE = (RuleId.ACCESSIBILITY, RuleId.RIDE_RESTRICTION)
PUBLIC = tuple(rule for rule in RuleId if rule not in PRIVATE)
SECRET = "guest g2: walking limit 7 min, NOT_RECOMMENDED_EXPECTANT\n(second line)"


def _violation(rule: RuleId) -> ConstraintViolation:
    return ConstraintViolation(
        rule=rule, message=SECRET, stop_id="stop-3", suggestion=f"avoid {SECRET}"
    )


@pytest.mark.parametrize("rule", PRIVATE, ids=lambda r: r.value)
def test_a_private_violation_keeps_its_rule_and_stop_but_not_its_values(
    rule: RuleId,
) -> None:
    public = public_violation(_violation(rule))

    assert (public.rule, public.stop_id) == (rule, "stop-3")
    for text in (public.message, public.suggestion or ""):
        assert "g2" not in text and "7 min" not in text and "EXPECTANT" not in text


@pytest.mark.parametrize("rule", PUBLIC, ids=lambda r: r.value)
def test_other_violations_pass_through_unchanged(rule: RuleId) -> None:
    violation = _violation(rule)

    assert public_violation(violation) == violation


def test_public_check_redacts_each_violation_in_order() -> None:
    result = CheckResult(
        valid=False,
        violations=[_violation(RuleId.HEIGHT), _violation(RuleId.ACCESSIBILITY)],
    )

    public = public_check(result)

    assert public.valid is False
    assert [v.rule for v in public.violations] == [RuleId.HEIGHT, RuleId.ACCESSIBILITY]
    assert public.violations[0].message == SECRET
    assert SECRET not in public.violations[1].message


class _Optimizer:
    def build_plan(self, constraints: Any, **kwargs: Any) -> Any:
        return factories.plan(stops=[])


class _Checker:
    def __init__(self, rule: RuleId) -> None:
        self.rule = rule

    def check(self, **kwargs: Any) -> CheckResult:
        return CheckResult(valid=False, violations=[_violation(self.rule)])


def _resolve_failure(rule: RuleId) -> str:
    """The failure the real re-solve loop writes when ``rule`` cannot be repaired."""
    result = PlannerResolveLoop(_Optimizer(), _Checker(rule), max_attempts=3).resolve(
        constraints=factories.party_constraints(),
        context=factories.live_context(),
        utilities={},
        now=factories.plan().provenance.retrieved_at,
        park=factories.park(),
    )
    assert result.valid is False and result.fatal_error
    return result.fatal_error


@pytest.mark.parametrize("rule", PRIVATE, ids=lambda r: r.value)
def test_a_resolve_failure_on_a_private_rule_keeps_only_the_rule(rule: RuleId) -> None:
    failure = _resolve_failure(rule)
    assert SECRET in failure  # the loop's own wording, which must not leave

    public = public_failure(failure)

    assert (
        public
        == f"Infeasible constraint set: {rule.value} (details stay in the guest's session)"
    )


def test_a_resolve_failure_on_another_rule_is_unchanged() -> None:
    failure = _resolve_failure(RuleId.HEIGHT)

    assert public_failure(failure) == failure
