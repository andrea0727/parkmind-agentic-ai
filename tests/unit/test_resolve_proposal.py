"""ResolveProposalUseCase: a decision on a proposal that is no longer PENDING (P0-33)."""

import factories
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import ApprovalStatus
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
