"""Re-normalizing a snapshot built by normalizer 1 (issue #69): the rebuild comes
from the stored raw payload, so the old meet-and-greet window disappears."""

from capture import NOW, Provider
from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository

from parkmind.services.clients.normalization import NORMALIZER_VERSION
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector
from parkmind.services.use_cases.renormalize_snapshots import (
    renormalize_snapshots,
    stale_snapshot_ids,
)

MEET_ARIEL = "012a211b-4c91-451c-8a0e-5e3ab398eda8"  # Operating 09:30-17:30 window


def test_a_version_1_snapshot_is_rebuilt_without_meet_and_greet_windows() -> None:
    provider = Provider()
    snapshots, ids = InMemorySnapshotRepository(), InMemoryIdMappingRepository()
    sid = (
        SnapshotCollector(provider.parks(), provider.weather(), snapshots, ids)
        .collect(now=NOW)
        .snapshot_id
    )
    # What normalizer 1 stored: the Operating window read as a "show at 09:30".
    row = snapshots.rows[sid]
    window_start = NOW.replace(hour=9, minute=30, second=0)
    row["live_context"] = row["live_context"].model_copy(
        update={
            "showtimes": {**row["live_context"].showtimes, MEET_ARIEL: [window_start]}
        }
    )
    row["version"] = 1

    assert stale_snapshot_ids(snapshots) == [sid]
    report = renormalize_snapshots(snapshots, ids, stale_snapshot_ids(snapshots))

    assert [(o.snapshot_id, o.status) for o in report.outcomes] == [(sid, "rebuilt")]
    rebuilt = snapshots.get(sid)
    assert rebuilt is not None and MEET_ARIEL not in rebuilt.showtimes
    assert snapshots.rows[sid]["version"] == NORMALIZER_VERSION == 2
    assert stale_snapshot_ids(snapshots) == []
