"""LoadContextUseCase: source order (live -> stored snapshot -> error) and per-party accessibility coverage."""

from datetime import timedelta

import pytest
from elicit_support import FakeSessionStore
from fakes import InMemorySnapshotRepository
from planning_support import (
    NOW,
    FakeCollector,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.core.contracts import (
    AccessibilityRequirements,
    MobilityRequirement,
    RideRestriction,
)
from parkmind.services.clients.knowledge.in_memory import InMemoryKnowledgeStore
from parkmind.services.clients.themeparks_errors import ThemeParksUnavailableError
from parkmind.services.use_cases.collect_snapshot import CollectResult
from parkmind.services.use_cases.load_context import (
    ContextUnavailableError,
    LoadContextUseCase,
)
from parkmind.services.use_cases.snapshot_normalization import ACCESSIBILITY_GAP

THREAD = "t1"


def _requirements(guest_id: str, **kwargs) -> AccessibilityRequirements:
    return AccessibilityRequirements(guest_id=guest_id, consent=True, **kwargs)


def _use_case(**deps_kwargs) -> LoadContextUseCase:
    return LoadContextUseCase(factory_for(make_deps(**deps_kwargs)))


def test_falls_back_to_the_latest_snapshot_when_the_collector_fails():
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(snapshot_id="stored"))
    collector = FakeCollector(error=ThemeParksUnavailableError("provider down"))

    loaded = _use_case(snapshots=snapshots, collector=collector).execute(THREAD, [], NOW)

    assert collector.calls == 1
    assert loaded.source == "snapshot"
    assert loaded.live_context.snapshot_id == "stored"
    assert loaded.age == NOW - loaded.live_context.retrieved_at


def test_a_collected_snapshot_is_live():
    live = snapshot_context(snapshot_id="fresh")
    collector = FakeCollector(CollectResult(snapshot_id="fresh", created=True, live_context=live))

    loaded = _use_case(collector=collector).execute(THREAD, [], NOW)

    assert loaded.source == "live"
    assert loaded.live_context.snapshot_id == "fresh"


def test_an_existing_window_is_read_back_from_the_repository():
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(snapshot_id="window"))
    collector = FakeCollector(CollectResult(snapshot_id="window", created=False))

    loaded = _use_case(snapshots=snapshots, collector=collector).execute(THREAD, [], NOW)

    assert loaded.source == "live"
    assert loaded.live_context.snapshot_id == "window"


def test_without_a_collector_it_plans_from_the_stored_snapshot():
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context())

    assert _use_case(snapshots=snapshots).execute(THREAD, [], NOW).source == "snapshot"


def test_no_live_data_and_no_snapshot_is_a_typed_error():
    collector = FakeCollector(error=ThemeParksUnavailableError("provider down"))

    with pytest.raises(ContextUnavailableError):
        _use_case(collector=collector).execute(THREAD, [], NOW)


def test_now_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        _use_case().execute(THREAD, [], NOW.replace(tzinfo=None))


def test_coverage_is_complete_when_every_guest_has_a_result_for_every_attraction():
    sessions = FakeSessionStore()
    sessions.put(THREAD, _requirements("g1"))
    sessions.put(THREAD, _requirements("g2", mobility_requirements=[MobilityRequirement.LIMITED_WALKING]))
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context())
    use_case = _use_case(sessions=sessions, snapshots=snapshots)

    live = use_case.execute(THREAD, ["g1", "g2"], NOW).live_context

    assert live.coverage.accessibility_checks_complete
    assert ACCESSIBILITY_GAP not in live.coverage.coverage_gaps
    assert len(live.accessibility_results) == 2 * 7
    assert {r.guest_id for r in live.accessibility_results} == {"g1", "g2"}


def test_an_unreadable_guest_keeps_coverage_incomplete_and_is_named():
    sessions = FakeSessionStore()
    sessions.put(THREAD, _requirements("g1"))
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context())

    live = _use_case(sessions=sessions, snapshots=snapshots).execute(THREAD, ["g1", "g2"], NOW).live_context

    assert not live.coverage.accessibility_checks_complete
    assert any("g2" in gap for gap in live.coverage.coverage_gaps)
    assert {r.guest_id for r in live.accessibility_results} == {"g1"}


def test_an_uncovered_attraction_fails_closed_for_a_restricted_guest():
    sessions = FakeSessionStore()
    sessions.put(THREAD, _requirements("g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HEART_CONDITION]))
    sessions.put(THREAD, _requirements("g2"))
    knowledge = InMemoryKnowledgeStore({"id-tron": []}, corpus_version="t")
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context())

    live = _use_case(sessions=sessions, snapshots=snapshots, knowledge=knowledge).execute(
        THREAD, ["g1", "g2"], NOW
    ).live_context

    by_pair = {(r.guest_id, r.attraction_id): r for r in live.accessibility_results}
    assert by_pair[("g1", "id-tron")].eligible
    assert not by_pair[("g1", "id-space")].eligible
    assert by_pair[("g2", "id-space")].eligible


def test_results_of_a_checked_guest_replace_stale_ones_in_the_snapshot():
    sessions = FakeSessionStore()
    sessions.put(THREAD, _requirements("g1"))
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context())
    use_case = _use_case(sessions=sessions, snapshots=snapshots)

    first = use_case.execute(THREAD, ["g1"], NOW).live_context
    again = use_case.execute(THREAD, ["g1"], NOW + timedelta(minutes=1)).live_context

    assert len(first.accessibility_results) == len(again.accessibility_results)
