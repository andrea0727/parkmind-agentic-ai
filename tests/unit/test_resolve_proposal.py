"""ResolveProposalUseCase: activation, progress carry-over and the behavior log."""

from datetime import timedelta

import factories
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import (
    ApprovalStatus,
    BehaviorEventType,
    GuestRole,
    RejectionReason,
)
from parkmind.services.use_cases.resolve_proposal import (
    ProposalNoLongerPendingError,
    ResolveProposalUseCase,
)

NOW = factories.NOW
PARTY = [
    factories.guest(guest_id="g1"),
    factories.guest(guest_id="g2", role=GuestRole.CHILD, height_cm=110),
]


@pytest.fixture
def deps():
    deps = make_deps()
    deps.plans.save("t1", factories.plan())
    deps.proposals.save("t1", factories.proposal())
    return deps


@pytest.mark.parametrize("decision", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_a_superseded_proposal_cannot_be_decided_and_nothing_is_activated(
    deps, decision
):
    deps.proposals.supersede_pending("t1", at=NOW)

    with pytest.raises(ProposalNoLongerPendingError) as raised:
        ResolveProposalUseCase(factory_for(deps)).execute(
            "t1", "prop_1", decision, at=NOW, guests=PARTY
        )

    assert raised.value.status is ApprovalStatus.SUPERSEDED
    assert deps.plans.get_active("t1") is None
    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.SUPERSEDED


def test_a_pending_proposal_is_still_approved_and_activated(deps):
    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
    )

    assert deps.plans.get_active("t1").plan_id == "plan_1"


def test_a_decision_already_applied_reports_its_own_status(deps):
    use_case = ResolveProposalUseCase(factory_for(deps))
    use_case.execute("t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY)

    with pytest.raises(ProposalNoLongerPendingError) as raised:
        use_case.execute("t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY)

    assert raised.value.status is ApprovalStatus.APPROVED


@pytest.fixture
def replan_deps():
    """plan_0 is active with reported progress; plan_1 is proposed to replace it."""
    deps = make_deps()
    deps.plans.save("t1", factories.plan(plan_id="plan_0"))
    deps.proposals.save(
        "t1",
        factories.proposal(
            proposal_id="prop_0", base_plan_id="plan_0", candidate_plan_id="plan_0"
        ),
    )
    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_0", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
    )
    deps.execution_states.save(
        factories.execution_state(plan_id="plan_0", completed_stop_ids=["a1"])
    )
    deps.plans.save("t1", factories.plan(plan_id="plan_1"))
    deps.proposals.save("t1", factories.proposal())
    return deps


def test_approving_a_replan_carries_the_reported_progress_to_the_new_plan(replan_deps):
    deps = replan_deps

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
    )

    carried = deps.execution_states.get("plan_1")
    assert carried is not None and carried.completed_stop_ids == ["a1"]
    assert deps.execution_states.get("plan_0").plan_id == "plan_0"


def test_progress_reported_while_the_guests_decide_is_not_lost(replan_deps):
    deps = replan_deps
    later = NOW + timedelta(minutes=10)
    deps.execution_states.save(
        factories.execution_state(
            plan_id="plan_0", completed_stop_ids=["a1", "a2"], as_of=later
        )
    )

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=later, guests=PARTY
    )

    assert deps.execution_states.get("plan_1").completed_stop_ids == ["a1", "a2"]


@pytest.mark.parametrize("decision", [ApprovalStatus.REJECTED, ApprovalStatus.SUPERSEDED])
def test_a_replan_that_is_not_approved_leaves_no_progress_behind(replan_deps, decision):
    deps = replan_deps
    if decision is ApprovalStatus.SUPERSEDED:
        deps.proposals.supersede_pending("t1", at=NOW)
    else:
        ResolveProposalUseCase(factory_for(deps)).execute(
            "t1", "prop_1", decision, at=NOW, guests=PARTY
        )

    assert deps.execution_states.get("plan_1") is None


def test_no_progress_is_invented_when_none_was_reported(deps):
    deps.plans.save("t1", factories.plan(plan_id="plan_0"))

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
    )

    assert deps.execution_states.get("plan_1") is None


def test_approving_an_initial_plan_leaves_its_own_progress_alone(deps):
    deps.proposals.save("t1", factories.proposal(base_plan_id="plan_1"))
    state = factories.execution_state(plan_id="plan_1")
    deps.execution_states.save(state)

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
    )

    assert deps.execution_states.get("plan_1") == state


def _decide(deps, status, **kwargs):
    return ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", status, at=NOW, guests=PARTY, **kwargs
    )


@pytest.mark.parametrize(
    ("status", "event_type", "decision"),
    [
        (ApprovalStatus.APPROVED, BehaviorEventType.PROPOSAL_ACCEPTED, "ACCEPTED"),
        (ApprovalStatus.REJECTED, BehaviorEventType.PROPOSAL_REJECTED, "REJECTED"),
        (ApprovalStatus.EDITED, BehaviorEventType.PROPOSAL_EDITED, "EDITED"),
    ],
)
def test_every_decision_writes_one_behavior_entry_per_guest(
    deps, status, event_type, decision
):
    edit = {"removed_attraction_ids": ["a1"]} if status is ApprovalStatus.EDITED else {}
    _decide(deps, status, attraction_ids=["a1"], **edit)

    for guest_id in ("t1:g1", "t1:g2"):
        (entry,) = deps.behavior.get(guest_id).entries
        assert entry.event_type is event_type
        assert entry.decision == decision
        assert (entry.proposal_id, entry.plan_id) == ("prop_1", "plan_1")
        assert entry.attraction_ids == ["a1"]
        assert entry.timestamp == NOW


def test_the_log_keys_guests_by_thread_and_keeps_only_their_role(deps):
    _decide(deps, ApprovalStatus.APPROVED)

    assert deps.behavior.get("g1").entries == []
    child = deps.guests.get("t1:g2")
    assert child is not None and child.role is GuestRole.CHILD and child.height_cm is None


def test_a_rejection_logs_its_reason_and_activates_nothing(deps):
    resolved = _decide(
        deps, ApprovalStatus.REJECTED, rejection_reason=RejectionReason.TOO_MUCH_WALKING
    )

    assert resolved.rejection_reason is RejectionReason.TOO_MUCH_WALKING
    assert deps.plans.get_active("t1") is None
    (entry,) = deps.behavior.get("t1:g1").entries
    assert entry.rejection_reason is RejectionReason.TOO_MUCH_WALKING


def test_an_edited_proposal_activates_nothing(deps):
    resolved = _decide(
        deps, ApprovalStatus.EDITED, attraction_ids=["a1", "a2"], removed_attraction_ids=["a2"]
    )

    assert resolved.approval_status is ApprovalStatus.EDITED
    assert deps.plans.get_active("t1") is None


def test_an_edit_logs_what_it_removed_apart_from_what_it_added(deps):
    _decide(
        deps,
        ApprovalStatus.EDITED,
        attraction_ids=["a1", "a2"],
        removed_attraction_ids=["a2"],
        added_attraction_ids=["a3"],
    )

    (entry,) = deps.behavior.get("t1:g1").entries
    assert entry.attraction_ids == ["a1", "a2"]
    assert entry.removed_attraction_ids == ["a2"]
    assert entry.added_attraction_ids == ["a3"]


def test_an_edit_that_names_no_change_is_refused_and_changes_nothing(deps):
    with pytest.raises(ValueError, match="removed and/or added"):
        _decide(deps, ApprovalStatus.EDITED, attraction_ids=["a1"])

    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.PENDING
    assert deps.behavior.get("t1:g1").entries == []


@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_only_an_edit_may_log_removed_or_added_attractions(deps, status):
    with pytest.raises(ValueError, match="only an edit"):
        _decide(deps, status, added_attraction_ids=["a3"])

    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.PENDING


def test_a_decision_applied_twice_is_logged_once(deps):
    _decide(deps, ApprovalStatus.APPROVED)
    with pytest.raises(ProposalNoLongerPendingError):
        _decide(deps, ApprovalStatus.APPROVED)

    assert len(deps.behavior.get("t1:g1").entries) == 1


def test_a_superseded_proposal_logs_nothing(deps):
    deps.proposals.supersede_pending("t1", at=NOW)

    with pytest.raises(ProposalNoLongerPendingError):
        _decide(deps, ApprovalStatus.APPROVED)

    assert deps.guests.list_all() == []


def test_a_decision_for_nobody_is_refused_before_anything_is_written(deps):
    with pytest.raises(ValueError, match="guests are required"):
        ResolveProposalUseCase(factory_for(deps)).execute(
            "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guests=[]
        )

    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.PENDING
    assert deps.plans.get_active("t1") is None


@pytest.mark.parametrize("status", [ApprovalStatus.PENDING, ApprovalStatus.SUPERSEDED])
def test_pending_and_superseded_are_not_decisions(deps, status):
    with pytest.raises(ValueError, match="not a decision"):
        _decide(deps, status)

    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.PENDING
