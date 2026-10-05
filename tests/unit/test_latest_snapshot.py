"""P0-11 Done-when 3: "The application can retrieve the most recent valid snapshot and its age."

Age is measured at the ``now`` passed in -- the exact bug found in PR #63's rule 11,
where age was measured against a plan's first stop.
"""

from datetime import timedelta

import factories
import pytest
from fakes import InMemorySnapshotRepository

from parkmind.services.ports import StoredDataError
from parkmind.services.use_cases.latest_snapshot import latest_valid_snapshot

NOW = factories.NOW


def _repo(*ages_minutes: int) -> InMemorySnapshotRepository:
    repo = InMemorySnapshotRepository()
    for minutes in ages_minutes:
        repo.save(
            factories.live_context(snapshot_id=f"snap_{minutes}", retrieved_at=NOW - timedelta(minutes=minutes)),
            {},
            [],
            normalizer_version=1,
        )
    return repo


def test_returns_the_newest_snapshot_and_its_age_at_now() -> None:
    latest = latest_valid_snapshot(_repo(45, 10, 90), now=NOW)

    assert latest is not None
    assert latest.live_context.snapshot_id == "snap_10"
    assert latest.age == timedelta(minutes=10)
    assert latest.fresh is True


def test_age_is_measured_from_now_not_from_the_snapshot_day() -> None:
    """A snapshot taken this morning is 7 hours old at 17:00 -- whatever a plan says."""
    latest = latest_valid_snapshot(_repo(0), now=NOW + timedelta(hours=7))

    assert latest is not None
    assert latest.age == timedelta(hours=7)
    assert latest.fresh is False


def test_stale_latest_is_returned_with_fresh_false() -> None:
    latest = latest_valid_snapshot(_repo(31), now=NOW)

    assert latest is not None and latest.fresh is False  # §43: caller decides; rule 11 fails it


def test_max_age_is_configurable() -> None:
    latest = latest_valid_snapshot(_repo(31), now=NOW, max_age=timedelta(hours=1))

    assert latest is not None and latest.fresh is True


def test_a_snapshot_dated_after_now_is_not_fresh() -> None:
    latest = latest_valid_snapshot(_repo(0), now=NOW - timedelta(minutes=5))

    assert latest is not None and latest.age < timedelta(0) and latest.fresh is False


def test_an_invalid_newer_row_is_skipped_and_named() -> None:
    class CorruptNewest(InMemorySnapshotRepository):
        def get(self, snapshot_id):  # type: ignore[no-untyped-def]
            if snapshot_id == "snap_1":
                raise StoredDataError("stored snapshot no longer matches LiveContext")
            return super().get(snapshot_id)

    repo = CorruptNewest()
    for minutes in (1, 20):
        repo.save(factories.live_context(snapshot_id=f"snap_{minutes}", retrieved_at=NOW - timedelta(minutes=minutes)), {}, [], normalizer_version=1)

    latest = latest_valid_snapshot(repo, now=NOW)

    assert latest is not None
    assert latest.live_context.snapshot_id == "snap_20"
    assert latest.skipped == ["snap_1"]


def test_no_snapshots_is_none() -> None:
    assert latest_valid_snapshot(InMemorySnapshotRepository(), now=NOW) is None


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="aware"):
        latest_valid_snapshot(_repo(0), now=NOW.replace(tzinfo=None))
