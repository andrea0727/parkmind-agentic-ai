"""ResolveProposalUseCase: a decision on a proposal that is no longer PENDING (P0-33)."""

from datetime import timedelta

import factories
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import ApprovalStatus, BehaviorEventType, RejectionReason
from parkmind.services.use_cases.resolve_proposal import (
    ProposalNoLongerPendingError,
    ResolveProposalUseCase,
)

NOW = factories.NOW


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
            "t1", "prop_1", decision, at=NOW
        )

    assert raised.value.status is ApprovalStatus.SUPERSEDED
    assert deps.plans.get_active("t1") is None
    assert deps.proposals.get("prop_1").approval_status is ApprovalStatus.SUPERSEDED


def test_a_pending_proposal_is_still_approved_and_activated(deps):
    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW
    )

    assert deps.plans.get_active("t1").plan_id == "plan_1"


def test_a_decision_already_applied_reports_its_own_status(deps):
    use_case = ResolveProposalUseCase(factory_for(deps))
    use_case.execute("t1", "prop_1", ApprovalStatus.APPROVED, at=NOW)

    with pytest.raises(ProposalNoLongerPendingError) as raised:
        use_case.execute("t1", "prop_1", ApprovalStatus.APPROVED, at=NOW)

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
        "t1", "prop_0", ApprovalStatus.APPROVED, at=NOW
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
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW
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
        "t1", "prop_1", ApprovalStatus.APPROVED, at=later
    )

    assert deps.execution_states.get("plan_1").completed_stop_ids == ["a1", "a2"]


@pytest.mark.parametrize("decision", [ApprovalStatus.REJECTED, ApprovalStatus.SUPERSEDED])
def test_a_replan_that_is_not_approved_leaves_no_progress_behind(replan_deps, decision):
    deps = replan_deps
    if decision is ApprovalStatus.SUPERSEDED:
        deps.proposals.supersede_pending("t1", at=NOW)
    else:
        ResolveProposalUseCase(factory_for(deps)).execute(
            "t1", "prop_1", decision, at=NOW
        )

    assert deps.execution_states.get("plan_1") is None


def test_no_progress_is_invented_when_none_was_reported(deps):
    deps.plans.save("t1", factories.plan(plan_id="plan_0"))

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW
    )

    assert deps.execution_states.get("plan_1") is None


def test_approving_an_initial_plan_leaves_its_own_progress_alone(deps):
    deps.proposals.save("t1", factories.proposal(base_plan_id="plan_1"))
    state = factories.execution_state(plan_id="plan_1")
    deps.execution_states.save(state)

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW
    )

    assert deps.execution_states.get("plan_1") == state

def test_resolve_proposal_approve_activates_plan_and_logs_behavior(deps):
    use_case = ResolveProposalUseCase(factory_for(deps))
    resolved = use_case.execute(
        "t1", "prop_1", ApprovalStatus.APPROVED, at=NOW, guest_ids=["guest-1"]
    )

    assert resolved.approval_status == ApprovalStatus.APPROVED
    assert deps.plans.get_active("t1") is not None

    entries = deps.behavior.get_by_guest("guest-1")
    assert len(entries) == 1
    assert entries[0].event_type == BehaviorEventType.PROPOSAL_ACCEPTED


def test_resolve_proposal_reject_stores_reason_and_does_not_activate(deps):
    use_case = ResolveProposalUseCase(factory_for(deps))
    resolved = use_case.execute(
        "t1", "prop_1", ApprovalStatus.REJECTED, at=NOW, 
        rejection_reason=RejectionReason.TOO_MUCH_WALKING, guest_ids=["guest-1"]
    )

    assert resolved.approval_status == ApprovalStatus.REJECTED
    assert resolved.rejection_reason == RejectionReason.TOO_MUCH_WALKING
    assert deps.plans.get_active("t1") is None

    entries = deps.behavior.get_by_guest("guest-1")
    assert len(entries) == 1
    assert entries[0].event_type == BehaviorEventType.PROPOSAL_REJECTED
    assert entries[0].rejection_reason == RejectionReason.TOO_MUCH_WALKING


def test_resolve_proposal_edit_resolves_as_edited_and_does_not_activate(deps):
    use_case = ResolveProposalUseCase(factory_for(deps))
    resolved = use_case.execute(
        "t1", "prop_1", ApprovalStatus.EDITED, at=NOW, guest_ids=["guest-1"]
    )

    assert resolved.approval_status == ApprovalStatus.EDITED
    assert deps.plans.get_active("t1") is None

    entries = deps.behavior.get_by_guest("guest-1")
    assert len(entries) == 1
    assert entries[0].event_type == BehaviorEventType.PROPOSAL_EDITED