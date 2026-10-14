"""ConversationService: which graph owns a thread, and what each answer may resume."""

import factories
import pytest
from conversation_support import COMPLETE, FIRST_REQUEST, MISSING, make_rig
from planning_support import NOW

from parkmind.agents.elicit_agent import ExtractorUnavailableError
from parkmind.core.contracts import ApprovalStatus, EventType, RejectionReason
from parkmind.core.conversation import (
    HardConstraintAnswer,
    NotExpectedError,
    PlanEditRequest,
    ServiceUnavailableError,
    StaleProposalError,
    TurnStatus,
    UnknownAttractionError,
    UnknownThreadError,
)
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError, load_catalog

CONFIRM = HardConstraintAnswer(confirmed=True, consent=True)


def _to_proposal(rig):
    thread = rig.service.start()
    first = rig.service.say(thread, FIRST_REQUEST)
    assert first.status is TurnStatus.NEEDS_CONFIRMATION
    return thread, rig.service.confirm_constraints(thread, CONFIRM)


def test_a_request_reaches_the_hard_constraint_confirmation():
    rig = make_rig()
    thread = rig.service.start()

    turn = rig.service.say(thread, FIRST_REQUEST)

    assert turn.status is TurnStatus.NEEDS_CONFIRMATION
    assert turn.confirmation.echo
    assert rig.service.pending_proposal(thread) is None
    assert rig.service.active_plan(thread) is None


def test_confirming_yields_a_proposal_and_approving_activates_it():
    rig = make_rig()
    thread, turn = _to_proposal(rig)

    assert turn.status is TurnStatus.NEEDS_APPROVAL
    proposal, plan = rig.service.pending_proposal(thread)
    assert proposal.proposal_id == turn.proposal.proposal_id
    assert plan.plan_id == turn.candidate_plan.plan_id
    assert rig.service.active_plan(thread) is None

    done = rig.service.decide(thread, proposal.proposal_id, ApprovalStatus.APPROVED)

    assert done.status is TurnStatus.DONE
    assert rig.service.active_plan(thread).plan_id == plan.plan_id
    assert rig.service.pending_proposal(thread) is None


def test_rejecting_activates_nothing():
    rig = make_rig()
    thread, turn = _to_proposal(rig)

    rig.service.decide(
        thread,
        turn.proposal.proposal_id,
        ApprovalStatus.REJECTED,
        RejectionReason.TOO_MUCH_WALKING,
    )

    assert rig.service.active_plan(thread) is None


def test_a_missing_answer_resumes_the_extraction():
    rig = make_rig(MISSING["extraction"], COMPLETE["extraction"])
    thread = rig.service.start()

    asked = rig.service.say(thread, " ".join(MISSING["messages"]))

    assert asked.status is TurnStatus.NEEDS_ANSWER
    assert asked.question
    assert "departure_time" in asked.missing
    assert asked.question not in asked.messages

    answered = rig.service.say(thread, "we leave at 8 pm")

    assert answered.status is TurnStatus.NEEDS_CONFIRMATION


def test_answers_that_do_not_fit_what_is_pending_are_refused():
    rig = make_rig()
    thread = rig.service.start()
    rig.service.say(thread, FIRST_REQUEST)

    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, "prop_x", ApprovalStatus.APPROVED)
    with pytest.raises(NotExpectedError):
        rig.service.say(thread, "something else")
    with pytest.raises(NotExpectedError):
        rig.service.confirm_events(thread, True)
    with pytest.raises(NotExpectedError):
        rig.service.report_events(thread, [])


def test_a_decision_on_another_proposal_is_refused():
    rig = make_rig()
    thread, _ = _to_proposal(rig)

    with pytest.raises(StaleProposalError):
        rig.service.decide(thread, "prop_other", ApprovalStatus.APPROVED)
    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, "prop_other", ApprovalStatus.PENDING)


def test_an_unknown_session_is_refused():
    rig = make_rig()

    with pytest.raises(UnknownThreadError):
        rig.service.say("nope", "hello")
    with pytest.raises(UnknownThreadError):
        rig.service.active_plan("nope")


def test_an_event_on_an_active_plan_opens_a_replan_proposal_the_service_can_decide():
    rig = make_rig()
    thread, turn = _to_proposal(rig)
    rig.service.decide(thread, turn.proposal.proposal_id, ApprovalStatus.APPROVED)
    active = rig.service.active_plan(thread)
    down = factories.event(
        event_id="ev_down",
        type=EventType.ATTRACTION_DOWN,
        attraction_id=active.stops[0].node_id,
        timestamp=NOW,
    )

    replan = rig.service.report_events(thread, [down])

    assert replan.status is TurnStatus.NEEDS_APPROVAL
    assert replan.proposal.base_plan_id == active.plan_id
    assert rig.service.active_plan(thread).plan_id == active.plan_id

    rig.service.decide(thread, replan.proposal.proposal_id, ApprovalStatus.APPROVED)

    assert rig.service.active_plan(thread).plan_id == replan.candidate_plan.plan_id


def test_an_event_before_any_plan_says_there_is_nothing_to_adjust():
    rig = make_rig()
    thread = rig.service.start()

    known = load_catalog(rig.deps)[0].node_id

    turn = rig.service.report_events(
        thread, [factories.event(event_id="e1", attraction_id=known, timestamp=NOW)]
    )

    assert turn.status is TurnStatus.DONE
    assert "nothing to adjust" in turn.messages[-1]


def test_an_event_naming_an_attraction_outside_the_catalog_is_refused():
    rig = make_rig()
    thread, turn = _to_proposal(rig)
    rig.service.decide(thread, turn.proposal.proposal_id, ApprovalStatus.APPROVED)

    with pytest.raises(UnknownAttractionError):
        rig.service.report_events(
            thread,
            [
                factories.event(
                    event_id="ev_ghost",
                    type=EventType.ATTRACTION_DOWN,
                    attraction_id="not_a_ride",
                    timestamp=NOW,
                )
            ],
        )


def _stop_ids(plan):
    return [stop.node_id for stop in plan.stops]


def test_an_edit_that_fits_comes_back_as_a_new_proposal():
    rig = make_rig()
    thread, turn = _to_proposal(rig)
    dropped = _stop_ids(turn.candidate_plan)[-1]

    edited = rig.service.decide(
        thread,
        turn.proposal.proposal_id,
        ApprovalStatus.EDITED,
        edit=PlanEditRequest(remove=(dropped,)),
    )

    assert edited.status is TurnStatus.NEEDS_APPROVAL and edited.error is None
    assert edited.proposal.proposal_id != turn.proposal.proposal_id
    assert dropped not in _stop_ids(edited.candidate_plan)
    assert rig.service.active_plan(thread) is None


def test_an_edit_that_does_not_fit_changes_nothing():
    rig = make_rig()
    thread, turn = _to_proposal(rig)

    rejected = rig.service.decide(
        thread,
        turn.proposal.proposal_id,
        ApprovalStatus.EDITED,
        edit=PlanEditRequest(remove=("not_a_stop",)),
    )

    assert rejected.status is TurnStatus.NEEDS_APPROVAL and rejected.error
    assert rejected.proposal.proposal_id == turn.proposal.proposal_id
    done = rig.service.decide(thread, turn.proposal.proposal_id, ApprovalStatus.APPROVED)
    assert done.status is TurnStatus.DONE


def test_an_edit_needs_something_to_change_and_an_edited_decision():
    rig = make_rig()
    thread, turn = _to_proposal(rig)
    pid = turn.proposal.proposal_id

    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, pid, ApprovalStatus.EDITED, edit=PlanEditRequest())
    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, pid, ApprovalStatus.EDITED)
    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, pid, ApprovalStatus.APPROVED, edit=PlanEditRequest(add=("x",)))


def test_a_failed_run_is_retried_with_the_same_answer_not_answered_again(monkeypatch):
    rig = make_rig()
    thread, turn = _to_proposal(rig)
    pid = turn.proposal.proposal_id
    proposals = rig.deps.proposals
    real_resolve = proposals.resolve
    attempts = []

    def flaky_resolve(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("database went away")
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(proposals, "resolve", flaky_resolve)

    with pytest.raises(RuntimeError):
        rig.service.decide(thread, pid, ApprovalStatus.APPROVED)
    with pytest.raises(NotExpectedError):
        rig.service.decide(thread, pid, ApprovalStatus.REJECTED)
    done = rig.service.decide(thread, pid, ApprovalStatus.APPROVED)

    assert done.status is TurnStatus.DONE
    assert rig.service.active_plan(thread) is not None


def test_an_unreachable_model_is_a_plain_message_and_the_thread_stays_usable():
    rig = make_rig(ExtractorUnavailableError("ConnectError"), COMPLETE["extraction"])
    thread = rig.service.start()

    down = rig.service.say(thread, FIRST_REQUEST)

    assert down.status is TurnStatus.DONE
    assert "nothing was planned" in down.messages[-1]
    assert "ConnectError" not in down.messages[-1]
    assert rig.service.pending_proposal(thread) is None

    back = rig.service.say(thread, FIRST_REQUEST)

    assert back.status is TurnStatus.NEEDS_CONFIRMATION


def test_an_outage_that_escapes_the_graph_reaches_the_guests_in_their_language(monkeypatch):
    rig = make_rig()
    thread = rig.service.start()
    graph = rig.service._graphs["initial"]

    def down(*args, **kwargs):
        raise PlanningUnavailableError("postgres://user:secret@db.internal:5432 refused")

    monkeypatch.setattr(graph, "invoke", down)

    with pytest.raises(ServiceUnavailableError) as raised:
        rig.service.say(thread, FIRST_REQUEST, "es")

    assert "No se propuso nada" in str(raised.value)
    assert "secret" not in str(raised.value)
