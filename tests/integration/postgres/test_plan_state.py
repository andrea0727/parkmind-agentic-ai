"""P0-12 Done-when: "Active plan vs candidate/proposal state is persisted distinctly."

Architecture section 23: Approve -> active plan; nothing else activates one, and
the LLM has no path to it. Section 43 [C18]: a concurrent event supersedes the
pending proposal.
"""

import factories
import psycopg
import pytest

from parkmind.core.contracts import ApprovalStatus, RejectionReason
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.clients.postgres.proposal_repository import (
    PostgresProposalRepository,
)
from parkmind.services.clients.postgres.provenance_repository import (
    PostgresProvenanceRepository,
)
from parkmind.services.ports import (
    InvalidStateTransitionError,
    NotApprovedError,
    NotFoundError,
    PendingProposalExistsError,
    PlanImmutableError,
    ProposalImmutableError,
)

NOW = factories.NOW


def _repos(conn: psycopg.Connection) -> tuple[PostgresPlanRepository, PostgresProposalRepository]:
    return PostgresPlanRepository(conn), PostgresProposalRepository(conn)


def _candidate(
    conn: psycopg.Connection,
    *,
    thread: str = "t1",
    plan_id: str = "plan_1",
    proposal_id: str = "prop_1",
) -> None:
    """Store a plan body plus a PENDING proposal for it."""
    plans, proposals = _repos(conn)
    plans.save(thread, factories.plan(plan_id=plan_id))
    proposals.save(
        thread, factories.proposal(proposal_id=proposal_id, candidate_plan_id=plan_id)
    )


# --------------------------------------------------------------------------- plans


def test_plan_round_trips_and_its_provenance_is_indexed(conn: psycopg.Connection) -> None:
    plans, _ = _repos(conn)
    plan = factories.plan(provenance=factories.provenance(snapshot_id="snap_7"))

    plans.save("t1", plan)

    assert plans.get("plan_1") == plan
    assert plans.get("nope") is None
    assert PostgresProvenanceRepository(conn).subjects_for_snapshot("snap_7") == [
        ("PLAN", "plan_1")
    ]


def test_saving_an_identical_plan_again_is_a_no_op(conn: psycopg.Connection) -> None:
    plans, _ = _repos(conn)

    plans.save("t1", factories.plan())
    plans.save("t1", factories.plan())

    count = conn.execute("SELECT count(*) AS n FROM plans").fetchone()
    assert count is not None and count["n"] == 1


def test_a_plan_body_is_immutable_once_stored(conn: psycopg.Connection) -> None:
    plans, _ = _repos(conn)
    original = factories.plan(objective_value=1.5)
    plans.save("t1", original)

    with pytest.raises(PlanImmutableError):
        plans.save("t1", factories.plan(objective_value=9.9))
    with pytest.raises(PlanImmutableError):
        plans.save("another_thread", original)

    assert plans.get("plan_1") == original


# ------------------------------------------------------------ candidate vs active


def test_candidate_is_not_active_until_approved(conn: psycopg.Connection) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn)

    assert plans.get_active("t1") is None
    with pytest.raises(NotApprovedError):
        plans.activate("t1", "plan_1", at=NOW)
    assert plans.get_active("t1") is None

    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)

    assert plans.get_active("t1") == factories.plan(plan_id="plan_1")


@pytest.mark.parametrize(
    "status",
    [ApprovalStatus.REJECTED, ApprovalStatus.EDITED, ApprovalStatus.SUPERSEDED],
)
def test_activate_requires_approved_proposal(
    conn: psycopg.Connection, status: ApprovalStatus
) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn)
    proposals.resolve("prop_1", status, at=NOW)

    with pytest.raises(NotApprovedError):
        plans.activate("t1", "plan_1", at=NOW)

    assert plans.get_active("t1") is None


def test_activating_an_unknown_plan_is_not_found(conn: psycopg.Connection) -> None:
    plans, _ = _repos(conn)

    with pytest.raises(NotFoundError):
        plans.activate("t1", "ghost", at=NOW)


def test_an_approval_in_another_thread_does_not_activate_this_thread(
    conn: psycopg.Connection,
) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn, thread="t2")
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)

    with pytest.raises(NotApprovedError):
        plans.activate("t1", "plan_1", at=NOW)

    assert plans.get_active("t1") is None


def test_activation_replaces_the_previous_active_plan_and_keeps_its_body(
    conn: psycopg.Connection,
) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn, plan_id="plan_1", proposal_id="prop_1")
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)
    _candidate(conn, plan_id="plan_2", proposal_id="prop_2")
    proposals.resolve("prop_2", ApprovalStatus.APPROVED, at=NOW)

    plans.activate("t1", "plan_2", at=NOW)

    active = plans.get_active("t1")
    assert active is not None and active.plan_id == "plan_2"
    assert plans.get("plan_1") is not None
    count = conn.execute("SELECT count(*) AS n FROM active_plans").fetchone()
    assert count is not None and count["n"] == 1


def test_pending_proposal_and_active_plan_live_in_distinct_tables(
    conn: psycopg.Connection,
) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn, plan_id="plan_1", proposal_id="prop_1")
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)
    _candidate(conn, plan_id="plan_2", proposal_id="prop_2")  # a new pending candidate

    active = conn.execute("SELECT plan_id FROM active_plans").fetchall()
    pending = conn.execute(
        "SELECT candidate_plan_id FROM proposals WHERE approval_status = 'PENDING'"
    ).fetchall()
    plan_columns = {
        row["column_name"]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'plans'"
        ).fetchall()
    }

    assert [row["plan_id"] for row in active] == ["plan_1"]
    assert [row["candidate_plan_id"] for row in pending] == ["plan_2"]
    assert [p.candidate_plan_id for p in proposals.list_pending("t1")] == ["plan_2"]
    # The pending candidate never leaks into the active slot ...
    active_plan = plans.get_active("t1")
    assert active_plan is not None and active_plan.plan_id == "plan_1"
    # ... and a plan body carries no state of its own that could be flipped.
    assert not {c for c in plan_columns if "active" in c or "status" in c}


# ------------------------------------------------------------------- proposals


@pytest.mark.parametrize(
    "status",
    [
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EDITED,
        ApprovalStatus.SUPERSEDED,
    ],
)
def test_a_proposal_cannot_be_created_already_decided(
    conn: psycopg.Connection, status: ApprovalStatus
) -> None:
    """Section 23: only a human decision, recorded through resolve(), moves a
    proposal out of PENDING -- otherwise activate() could be reached without one."""
    plans, proposals = _repos(conn)
    plans.save("t1", factories.plan())

    with pytest.raises(InvalidStateTransitionError):
        proposals.save("t1", factories.proposal(approval_status=status))

    assert proposals.get("prop_1") is None
    assert PostgresProvenanceRepository(conn).get("PROPOSAL", "prop_1") is None
    with pytest.raises(NotApprovedError):
        plans.activate("t1", "plan_1", at=NOW)


def test_proposal_requires_its_candidate_plan_to_exist(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)

    with pytest.raises(NotFoundError):
        proposals.save("t1", factories.proposal(candidate_plan_id="ghost"))


def test_a_refused_proposal_leaves_no_provenance_row(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)

    with pytest.raises(NotFoundError):
        proposals.save("t1", factories.proposal(candidate_plan_id="ghost"))

    assert PostgresProvenanceRepository(conn).get("PROPOSAL", "prop_1") is None


def test_proposal_round_trips_with_its_provenance(conn: psycopg.Connection) -> None:
    plans, proposals = _repos(conn)
    plans.save("t1", factories.plan())
    proposal = factories.proposal()

    proposals.save("t1", proposal)

    assert proposals.get("prop_1") == proposal
    assert proposals.get("nope") is None
    assert PostgresProvenanceRepository(conn).get("PROPOSAL", "prop_1") == proposal.provenance


def test_a_second_pending_proposal_in_a_thread_is_refused(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)
    _candidate(conn, plan_id="plan_1", proposal_id="prop_1")
    PostgresPlanRepository(conn).save("t1", factories.plan(plan_id="plan_2"))

    with pytest.raises(PendingProposalExistsError):
        proposals.save("t1", factories.proposal(proposal_id="prop_2", candidate_plan_id="plan_2"))

    # Another thread is unaffected.
    _candidate(conn, thread="t2", plan_id="plan_3", proposal_id="prop_3")
    assert [p.proposal_id for p in proposals.list_pending("t2")] == ["prop_3"]


def test_supersede_pending_proposal(conn: psycopg.Connection) -> None:
    plans, proposals = _repos(conn)
    _candidate(conn, thread="t1", plan_id="plan_1", proposal_id="prop_1")
    _candidate(conn, thread="t2", plan_id="plan_2", proposal_id="prop_2")

    superseded = proposals.supersede_pending("t1", at=NOW)

    assert superseded == ["prop_1"]
    stored = proposals.get("prop_1")
    assert stored is not None and stored.approval_status is ApprovalStatus.SUPERSEDED
    other = proposals.get("prop_2")
    assert other is not None and other.approval_status is ApprovalStatus.PENDING
    # The slot is free again: replanning may now propose a new candidate.
    plans.save("t1", factories.plan(plan_id="plan_3"))
    proposals.save("t1", factories.proposal(proposal_id="prop_3", candidate_plan_id="plan_3"))
    assert [p.proposal_id for p in proposals.list_pending("t1")] == ["prop_3"]


def test_superseding_nothing_returns_an_empty_list(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)

    assert proposals.supersede_pending("t1", at=NOW) == []


def test_proposal_resolution_is_final(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)
    _candidate(conn)
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)

    with pytest.raises(InvalidStateTransitionError):
        proposals.resolve("prop_1", ApprovalStatus.REJECTED, at=NOW)
    with pytest.raises(InvalidStateTransitionError):
        proposals.resolve("prop_1", ApprovalStatus.PENDING, at=NOW)
    with pytest.raises(NotFoundError):
        proposals.resolve("ghost", ApprovalStatus.APPROVED, at=NOW)

    stored = proposals.get("prop_1")
    assert stored is not None and stored.approval_status is ApprovalStatus.APPROVED


def test_rejection_reason_is_stored_with_the_resolution(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)
    _candidate(conn)

    resolved = proposals.resolve(
        "prop_1",
        ApprovalStatus.REJECTED,
        at=NOW,
        rejection_reason=RejectionReason.TOO_MUCH_WALKING,
    )

    assert resolved.approval_status is ApprovalStatus.REJECTED
    assert resolved.rejection_reason is RejectionReason.TOO_MUCH_WALKING
    assert proposals.get("prop_1") == resolved


def test_resaving_a_proposal_never_resets_its_resolution(conn: psycopg.Connection) -> None:
    _, proposals = _repos(conn)
    _candidate(conn)
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)

    proposals.save("t1", factories.proposal())  # a stale retry of the original PENDING save

    stored = proposals.get("prop_1")
    assert stored is not None and stored.approval_status is ApprovalStatus.APPROVED
    with pytest.raises(ProposalImmutableError):
        proposals.save("t1", factories.proposal(reason="a different body"))
