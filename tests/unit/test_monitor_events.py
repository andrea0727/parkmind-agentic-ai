"""The event monitor end to end on the 2026-09-27 capture.

Two collections through the real collector and normalizer; the second /live
payload closes Jungle Cruise with a fresh ``lastUpdated`` and also flips the
entity last updated in 2022. Only the fresh change may become an event.
"""

import copy
from dataclasses import replace
from datetime import UTC, timedelta

from capture import NOW, Provider, capture
from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository
from planning_support import factory_for, make_deps

from parkmind.core.contracts import EventType
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector
from parkmind.services.use_cases.monitor_events import MonitorEventsUseCase
from parkmind.services.use_cases.snapshot_normalization import reading_times

JUNGLE_CRUISE = "796b0a25-c51e-456e-9bb8-50a324e301b3"
FROM_2022 = "52ac3730-b955-452a-a7cc-17e3b06182ac"
FIRST = NOW
SECOND = NOW + timedelta(minutes=6)


def _second_live() -> dict:
    live = copy.deepcopy(capture("themeparks_live.json"))
    changed = (
        (SECOND - timedelta(minutes=1))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    for entity in live["liveData"]:
        if entity["id"] == JUNGLE_CRUISE:
            entity["status"] = "DOWN"
            entity["lastUpdated"] = changed
            entity.pop("queue", None)
        if entity["id"] == FROM_2022:
            entity["status"] = "CLOSED"  # lastUpdated stays 2022-08-12
    return live


def _monitor(collections):
    snapshots = InMemorySnapshotRepository()
    id_mappings = InMemoryIdMappingRepository()
    for at, live in collections:
        provider = Provider(live=live)
        SnapshotCollector(
            provider.parks(), provider.weather(), snapshots, id_mappings
        ).collect(now=at)
    deps = replace(make_deps(snapshots=snapshots), id_mappings=id_mappings)
    return MonitorEventsUseCase(factory_for(deps)), snapshots, id_mappings


def test_only_the_fresh_closure_becomes_an_event():
    monitor, _, _ = _monitor([(FIRST, None), (SECOND, _second_live())])

    result = monitor.detect(now=SECOND + timedelta(minutes=1))

    assert [(e.type, e.attraction_id) for e in result.candidates] == [
        (EventType.ATTRACTION_DOWN, JUNGLE_CRUISE)
    ]
    assert result.previous_snapshot_id != result.current_snapshot_id


def test_the_same_snapshots_give_the_same_events():
    monitor, _, _ = _monitor([(FIRST, None), (SECOND, _second_live())])
    later = SECOND + timedelta(minutes=1)

    assert monitor.detect(now=later) == monitor.detect(now=later)


def test_identical_collections_raise_nothing():
    monitor, _, _ = _monitor([(FIRST, None), (SECOND, None)])

    assert monitor.detect(now=SECOND + timedelta(minutes=1)).candidates == []


def test_a_single_snapshot_has_nothing_to_compare_with():
    monitor, _, _ = _monitor([(FIRST, None)])

    result = monitor.detect(now=FIRST + timedelta(minutes=1))

    assert result.candidates == [] and result.previous_snapshot_id is None


def test_checking_long_after_the_last_collection_raises_nothing():
    monitor, _, _ = _monitor([(FIRST, None), (SECOND, _second_live())])

    assert monitor.detect(now=SECOND + timedelta(minutes=45)).candidates == []


def test_a_snapshot_without_a_readable_raw_payload_fails_closed():
    monitor, snapshots, _ = _monitor([(FIRST, None), (SECOND, _second_live())])
    latest = snapshots.recent_ids(1)[0]
    snapshots.rows[latest]["raw"] = {"raw_schema": 99}

    assert monitor.detect(now=SECOND + timedelta(minutes=1)).candidates == []


def test_reading_times_are_read_without_recording_a_sighting():
    _, snapshots, id_mappings = _monitor([(FIRST, None)])
    before = dict(id_mappings.rows)
    raw = snapshots.get_raw_payload(snapshots.recent_ids(1)[0])

    times = reading_times(raw, id_mappings)

    assert id_mappings.rows == before
    assert times[FROM_2022] is not None and times[FROM_2022].year == 2022


def test_reading_times_look_the_ids_up_in_one_batch():
    _, snapshots, id_mappings = _monitor([(FIRST, None)])
    raw = snapshots.get_raw_payload(snapshots.recent_ids(1)[0])
    calls = id_mappings.resolve_many_calls

    times = reading_times(raw, id_mappings)

    assert len(times) > 1
    assert id_mappings.resolve_many_calls == calls + 1
