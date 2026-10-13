"""ReplanUseCase on a thread that has an approved plan (P0-34).

The plan, constraints, profiles and objective come from the initial planning graph's
checkpoint, exactly as the replanning graph reads them; the use case runs against the
same in-memory ports.
"""

from datetime import timedelta
from typing import Any

import factories
import pytest
from planning_support import NOW, factory_for
from test_replanning_graph import _down, _Replanning

from parkmind.core.contracts import ApprovalStatus, AttractionStatus, StopKind
from parkmind.services.use_cases.load_context import LoadContextUseCase
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.replan import ReplanRequest, ReplanUseCase
from parkmind.services.use_cases.resolve_proposal import ResolveProposalUseCase


@pytest.fixture
def r(monkeypatch: pytest.MonkeyPatch) -> _Replanning:
    return _Replanning(monkeypatch)


def _request(r: _Replanning, **overrides: Any) -> ReplanRequest:
    values = r.h.state()
    fields: dict[str, Any] = {
        "thread_id": "t1",
        "plan": r.active,
        "constraints": values["constraints"],
        "profiles": values["guest_profiles"],
        "accessibility_ref": values["accessibility_ref"],
        "live_context": values["live_context"],
        "objective": values["group_objective"],
        "now": NOW,
    }
    return ReplanRequest(**{**fields, **overrides})


def _use_case(r: _Replanning) -> ReplanUseCase:
    return ReplanUseCase(factory_for(r.deps))


def _attractions(plan: Any, *, after: Any = None) -> list[str]:
    return [
        s.node_id
        for s in plan.stops
        if s.kind is StopKind.ATTRACTION and (after is None or s.arrival_time >= after)
    ]


def test_a_replan_is_a_new_plan_that_keeps_what_was_already_done(r) -> None:
    outcome = _use_case(r).execute(_request(r))

    assert outcome.result.valid, outcome.result.fatal_error
    plan = outcome.result.plan
    assert plan is not None
    assert plan.plan_id != r.active.plan_id
    done = set(outcome.execution_state.completed_stop_ids)
    assert done <= {s.node_id for s in plan.stops}


def test_replanning_persists_nothing(r) -> None:
    plans = set(r.deps.plans.plans)
    proposals = len(r.proposals)
    active = r.active

    _use_case(r).execute(_request(r))

    assert set(r.deps.plans.plans) == plans
    assert len(r.proposals) == proposals
    assert r.active == active


def test_the_context_kept_for_state_holds_no_accessibility_results(r) -> None:
    outcome = _use_case(r).execute(_request(r))

    assert outcome.live_context.accessibility_results == []


def test_a_closure_the_thread_recorded_after_the_snapshot_is_avoided(r) -> None:
    ahead = [s for s in r.active.stops if s.arrival_time > NOW and s.kind is StopKind.ATTRACTION]
    assert ahead, "the fixture plan should have stops ahead of the party"
    closed = ahead[0].node_id
    event = _down(closed, minutes=1).model_copy(
        update={"timestamp": r.h.state()["live_context"].retrieved_at + timedelta(minutes=1)}
    )
    r.deps.events.record("t1", event)

    outcome = _use_case(r).execute(_request(r))

    assert outcome.result.valid, outcome.result.fatal_error
    assert closed not in _attractions(outcome.result.plan, after=NOW)
    assert outcome.live_context.statuses[closed] is AttractionStatus.DOWN


def test_without_a_recorded_closure_the_context_statuses_are_untouched(r) -> None:
    before = r.h.state()["live_context"].statuses

    outcome = _use_case(r).execute(_request(r))

    assert outcome.live_context.statuses == before


def test_progress_is_assumed_on_schedule_when_nobody_reported_it(r) -> None:
    later = NOW + timedelta(hours=2)

    outcome = _use_case(r).execute(_request(r, now=later))

    expected = {s.node_id for s in r.active.stops if s.departure_time <= later}
    assert set(outcome.execution_state.completed_stop_ids) == expected
    assert outcome.execution_state.as_of >= later


def test_reported_progress_is_trusted_over_the_schedule(r) -> None:
    first = r.active.stops[0].node_id
    stored = factories.execution_state(
        plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[first]
    )
    r.deps.execution_states.save(stored)

    outcome = _use_case(r).execute(_request(r, now=NOW + timedelta(hours=3)))

    assert outcome.execution_state.completed_stop_ids == [first]


def _propose_replan(r: _Replanning, plan: Any) -> None:
    ProposePlanUseCase(factory_for(r.deps)).execute(
        "t1",
        plan,
        proposal_id=f"prop_{plan.plan_id}",
        reason="r",
        explanation="e",
        at=NOW,
        base_plan_id=r.active.plan_id,
    )


def _approve_replan(r: _Replanning, plan: Any) -> None:
    ResolveProposalUseCase(factory_for(r.deps)).execute(
        "t1", f"prop_{plan.plan_id}", ApprovalStatus.APPROVED, at=NOW
    )


def test_a_second_replan_assumes_the_party_is_still_on_schedule(r) -> None:
    first = _use_case(r).execute(_request(r))
    assert first.result.plan is not None, first.result.fatal_error
    new_plan = first.result.plan
    _propose_replan(r, new_plan)
    later = NOW + timedelta(hours=4)

    second = _use_case(r).execute(_request(r, plan=new_plan, now=later))

    expected = {s.node_id for s in new_plan.stops if s.departure_time <= later}
    assert set(second.execution_state.completed_stop_ids) == expected
    assert len(expected) > len(first.execution_state.completed_stop_ids)


def test_an_assumed_progress_is_not_stored_for_the_new_plan(r) -> None:
    first = _use_case(r).execute(_request(r))
    assert first.result.plan is not None, first.result.fatal_error

    _propose_replan(r, first.result.plan)

    assert r.deps.execution_states.get(first.result.plan.plan_id) is None


def test_reported_progress_survives_the_plan_swap_and_is_still_trusted(r) -> None:
    done = r.active.stops[0].node_id
    r.deps.execution_states.save(
        factories.execution_state(plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[done])
    )
    first = _use_case(r).execute(_request(r))
    assert first.result.plan is not None, first.result.fatal_error
    _propose_replan(r, first.result.plan)
    _approve_replan(r, first.result.plan)

    second = _use_case(r).execute(
        _request(r, plan=first.result.plan, now=NOW + timedelta(hours=4))
    )

    assert second.execution_state.completed_stop_ids == [done]


def test_a_guest_with_no_stored_requirements_never_gets_a_valid_replan(r) -> None:
    r.deps.sessions.records.clear()
    ref = _request(r).accessibility_ref
    assert ref, "the thread should reference stored requirements"
    # Exactly what the graph's load_context step hands to the replan.
    reloaded = LoadContextUseCase(factory_for(r.deps)).execute("t1", ref, NOW).for_state()
    assert not reloaded.coverage.accessibility_checks_complete

    outcome = _use_case(r).execute(_request(r, live_context=reloaded))

    assert outcome.result.valid is False
    assert outcome.result.plan is None


def test_the_stored_requirements_shape_the_replan(r) -> None:
    [guest] = r.deps.sessions.records.values()
    assert guest.daily_walking_limit_minutes is None or guest.daily_walking_limit_minutes > 0
    tight = guest.model_copy(update={"daily_walking_limit_minutes": 1})
    r.deps.sessions.records[("t1", guest.guest_id)] = tight

    outcome = _use_case(r).execute(_request(r))

    # One minute of walking in the whole day cannot be met by any plan with a stop ahead.
    assert outcome.result.valid is False


def test_a_stale_context_that_cannot_be_refreshed_fails_closed(r, monkeypatch) -> None:
    stale = r.h.state()["live_context"].model_copy(
        update={"retrieved_at": NOW - timedelta(hours=3)}
    )

    def unavailable(*_a: Any, **_k: Any) -> Any:
        raise PlanningUnavailableError("no fresh snapshot")

    monkeypatch.setattr(LoadContextUseCase, "execute", unavailable)

    outcome = _use_case(r).execute(_request(r, live_context=stale))

    assert outcome.result.valid is False
    assert outcome.result.plan is None
    assert outcome.result.fatal_error


def test_a_stale_context_is_refreshed_and_the_fresh_one_is_kept_for_state(r, monkeypatch) -> None:
    stale = r.h.state()["live_context"].model_copy(
        update={"retrieved_at": NOW - timedelta(hours=3)}
    )
    fresh = r.h.state()["live_context"].model_copy(update={"snapshot_id": "fresh"})
    calls: list[Any] = []

    class Loaded:
        live_context = fresh

        def for_state(self) -> Any:
            return fresh

    def reload(this: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return Loaded()

    monkeypatch.setattr(LoadContextUseCase, "execute", reload)

    outcome = _use_case(r).execute(_request(r, live_context=stale))

    assert calls, "a stale snapshot must be reloaded before a plan is trusted"
    assert outcome.live_context.snapshot_id == "fresh"


# -- a caller without the thread (planner.replan, P0-27) ------------------------------


def test_progress_sent_inline_replaces_the_stored_one(r) -> None:
    first, second = (s.node_id for s in r.active.stops[:2])
    r.deps.execution_states.save(
        factories.execution_state(
            plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[first]
        )
    )
    sent = factories.execution_state(
        plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[first, second]
    )

    outcome = _use_case(r).execute(_request(r, execution_state=sent))

    assert outcome.execution_state.completed_stop_ids == [first, second]


def test_events_sent_inline_are_applied_and_never_recorded(r) -> None:
    ahead = [s for s in r.active.stops if s.arrival_time > NOW and s.kind is StopKind.ATTRACTION]
    closed = ahead[0].node_id
    event = _down(closed, minutes=1).model_copy(
        update={"timestamp": r.h.state()["live_context"].retrieved_at + timedelta(minutes=1)}
    )
    recorded = r.deps.events.list_for_thread("t1")

    outcome = _use_case(r).execute(_request(r, events=[event]))

    assert outcome.result.valid, outcome.result.fatal_error
    assert closed not in _attractions(outcome.result.plan, after=NOW)
    assert r.deps.events.list_for_thread("t1") == recorded


def test_progress_for_another_plan_is_refused(r) -> None:
    other = factories.execution_state(plan_id="someone_elses_plan", completed_stop_ids=[])

    with pytest.raises(ValueError, match="execution_state is for plan"):
        _request(r, execution_state=other)
