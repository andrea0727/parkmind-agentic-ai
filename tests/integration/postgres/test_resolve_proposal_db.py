"""Proposal resolution on real tables: a live party's decision lands in the behavior log.

The party's guests (``g1``, ``g2`` from elicitation) are not in ``guests``, and
``behavior_entries.guest_id`` references it: the decision registers them under their
thread-scoped id in the same transaction, so the foreign key holds and two parties'
``g1`` never share a log.
"""

from dataclasses import replace

import factories
import psycopg
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import ApprovalStatus, GuestRole, RejectionReason
from parkmind.services.clients.postgres import (
    PostgresBehaviorLogRepository,
    PostgresExecutionStateRepository,
    PostgresGuestRepository,
    PostgresPlanRepository,
    PostgresProposalRepository,
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


def _thread_with_a_pending_proposal(conn: psycopg.Connection, thread: str) -> None:
    PostgresPlanRepository(conn).save(thread, factories.plan(plan_id=f"plan_{thread}"))
    PostgresProposalRepository(conn).save(
        thread,
        factories.proposal(
            proposal_id=f"prop_{thread}",
            base_plan_id=f"plan_{thread}",
            candidate_plan_id=f"plan_{thread}",
        ),
    )


@pytest.fixture
def deps(conn: psycopg.Connection):
    return replace(
        make_deps(),
        plans=PostgresPlanRepository(conn),
        proposals=PostgresProposalRepository(conn),
        execution_states=PostgresExecutionStateRepository(conn),
        guests=PostgresGuestRepository(conn),
        behavior=PostgresBehaviorLogRepository(conn),
        transaction=lambda: conn.transaction(),
    )


@pytest.mark.parametrize(
    ("status", "reason", "decision"),
    [
        (ApprovalStatus.APPROVED, None, "ACCEPTED"),
        (ApprovalStatus.REJECTED, RejectionReason.TOO_MUCH_WALKING, "REJECTED"),
        (ApprovalStatus.EDITED, None, "EDITED"),
    ],
)
def test_an_unseeded_party_decision_is_logged_per_guest(
    conn, deps, status, reason, decision
) -> None:
    _thread_with_a_pending_proposal(conn, "t1")
    edit = (
        {"removed_attraction_ids": ["a1"], "added_attraction_ids": ["a2"]}
        if status is ApprovalStatus.EDITED
        else {}
    )

    ResolveProposalUseCase(factory_for(deps)).execute(
        "t1",
        "prop_t1",
        status,
        at=NOW,
        rejection_reason=reason,
        guests=PARTY,
        attraction_ids=["a1"],
        **edit,
    )

    behavior = PostgresBehaviorLogRepository(conn)
    for guest_id in ("t1:g1", "t1:g2"):
        (entry,) = behavior.get(guest_id).entries
        assert entry.decision == decision
        assert entry.rejection_reason is reason
        assert entry.attraction_ids == ["a1"]
        assert entry.removed_attraction_ids == edit.get("removed_attraction_ids", [])
        assert entry.added_attraction_ids == edit.get("added_attraction_ids", [])
    child = PostgresGuestRepository(conn).get("t1:g2")
    assert child is not None and child.role is GuestRole.CHILD and child.height_cm is None


def test_two_parties_never_share_a_guest_log(conn, deps) -> None:
    for thread in ("t1", "t2"):
        _thread_with_a_pending_proposal(conn, thread)
        ResolveProposalUseCase(factory_for(deps)).execute(
            thread, f"prop_{thread}", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
        )

    behavior = PostgresBehaviorLogRepository(conn)
    assert [e.proposal_id for e in behavior.get("t1:g1").entries] == ["prop_t1"]
    assert [e.proposal_id for e in behavior.get("t2:g1").entries] == ["prop_t2"]


def test_a_superseded_proposal_logs_nothing(conn, deps) -> None:
    _thread_with_a_pending_proposal(conn, "t1")
    PostgresProposalRepository(conn).supersede_pending("t1", at=NOW)

    with pytest.raises(ProposalNoLongerPendingError):
        ResolveProposalUseCase(factory_for(deps)).execute(
            "t1", "prop_t1", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
        )

    assert PostgresGuestRepository(conn).list_all() == []
