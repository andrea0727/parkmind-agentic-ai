"""P0-11 collector: idempotent windows, raw payloads kept, honest coverage, degrade.

Real clients over ``httpx.MockTransport`` serving the payloads captured together
on 2026-09-27 (tests/fixtures/snapshot_2026-09-27); only the repository ports
are in-memory fakes.
"""

from datetime import UTC, datetime, timedelta

import pytest
from capture import NOW, PARK_ID, Provider, capture
from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository

from parkmind.core.contracts import DataSource
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.normalization import NORMALIZER_VERSION
from parkmind.services.clients.themeparks_client import (
    ThemeParksUnavailableError,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.use_cases.collect_snapshot import (
    SnapshotCollector,
    snapshot_id_for,
)
from parkmind.services.use_cases.snapshot_normalization import ACCESSIBILITY_GAP


def _collector(provider: Provider) -> tuple[SnapshotCollector, InMemorySnapshotRepository]:
    snapshots = InMemorySnapshotRepository()
    return (
        SnapshotCollector(provider.parks(), provider.weather(), snapshots, InMemoryIdMappingRepository()),
        snapshots,
    )


# --------------------------------------------------------------- idempotency key


def test_snapshot_id_floors_now_to_the_window_in_park_time() -> None:
    assert snapshot_id_for(PARK_ID, NOW) == f"snap_{PARK_ID}_20260927T1100"
    # 15:04 UTC is 11:04 in New York: same window.
    utc = datetime(2026, 9, 27, 15, 4, tzinfo=UTC)
    assert snapshot_id_for(PARK_ID, utc) == f"snap_{PARK_ID}_20260927T1100"
    assert snapshot_id_for(PARK_ID, NOW + timedelta(minutes=3)) == f"snap_{PARK_ID}_20260927T1105"


def test_snapshot_id_rejects_naive_now_and_bad_intervals() -> None:
    with pytest.raises(ValueError, match="aware"):
        snapshot_id_for(PARK_ID, datetime(2026, 9, 27, 11, 0))  # noqa: DTZ001 -- naive on purpose
    with pytest.raises(ValueError, match="interval"):
        snapshot_id_for(PARK_ID, NOW, timedelta(0))


def test_rerun_in_the_same_window_writes_nothing_and_calls_no_provider() -> None:
    provider = Provider()
    collector, snapshots = _collector(provider)
    first = collector.collect(now=NOW)
    calls_after_first = len(provider.calls)

    second = collector.collect(now=NOW + timedelta(minutes=1))

    assert (first.created, second.created) == (True, False)
    assert second.snapshot_id == first.snapshot_id
    assert len(snapshots.rows) == 1
    assert len(provider.calls) == calls_after_first


# ------------------------------------------------------------- what gets stored


def test_raw_payloads_are_stored_verbatim_with_the_request_and_version() -> None:
    collector, snapshots = _collector(Provider())

    result = collector.collect(now=NOW)

    row = snapshots.rows[result.snapshot_id]
    raw = row["raw"]
    assert raw["themeparks"]["live"] == capture("themeparks_live.json")
    assert raw["themeparks"]["schedule"] == capture("themeparks_schedule.json")
    assert raw["open_meteo"]["hourly"] == capture("open_meteo_hourly.json")
    assert raw["request"]["service_date"] == "2026-09-27" and raw["degraded"] == []
    assert row["version"] == NORMALIZER_VERSION
    assert row["sources"] == [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO]
    assert row["live_context"].retrieved_at == NOW


def test_live_context_keeps_standby_waits_statuses_and_showtimes() -> None:
    collector, _ = _collector(Provider())

    context = collector.collect(now=NOW).live_context

    assert context is not None
    live = {e["id"]: e for e in capture("themeparks_live.json")["liveData"]}
    with_standby = {i for i, e in live.items() if ((e.get("queue") or {}).get("STANDBY") or {}).get("waitTime") is not None}
    assert set(context.waits) == with_standby - {PARK_ID}
    assert PARK_ID not in context.statuses
    assert all(t.tzinfo is PARK_TZ for times in context.showtimes.values() for t in times)


def test_coverage_is_honest_for_a_park_wide_snapshot() -> None:
    collector, _ = _collector(Provider())

    coverage = collector.collect(now=NOW).live_context.coverage  # type: ignore[union-attr]

    assert coverage.required_attractions_covered is True  # all 35 curated rides have a status
    assert coverage.weather_covered is True  # 24 hourly readings span 09:00-18:00
    assert coverage.accessibility_checks_complete is False  # the collector knows no party
    assert ACCESSIBILITY_GAP in coverage.coverage_gaps
    # 2026-09-27 was a party night: the 6 operating scheduled shows all have
    # showtimes; Happily Ever After, Starlight and the Philharmonic are CLOSED, so
    # they are not relevant today. Theater attractions (category SHOW, e.g. The
    # Hall of Presidents) are not scheduled shows and need no showtimes.
    assert coverage.required_shows_covered is True
    assert not any("show" in gap for gap in coverage.coverage_gaps)


def test_a_curated_attraction_missing_from_live_data_is_a_coverage_gap() -> None:
    curated = dict(MAGIC_KINGDOM_ATTRACTION_METADATA)
    curated["ghost-ride"] = next(iter(curated.values()))
    provider = Provider()
    collector = SnapshotCollector(
        provider.parks(), provider.weather(), InMemorySnapshotRepository(), InMemoryIdMappingRepository(), curated=curated
    )

    coverage = collector.collect(now=NOW).live_context.coverage  # type: ignore[union-attr]

    assert coverage.required_attractions_covered is False
    assert any("1 curated attraction(s) without a status" in gap for gap in coverage.coverage_gaps)


# --------------------------------------------------------------------- degrade


def test_weather_outage_still_writes_a_snapshot_without_open_meteo() -> None:
    collector, snapshots = _collector(Provider(weather_status=503))

    result = collector.collect(now=NOW)

    assert result.created is True
    assert result.data_sources == [DataSource.THEMEPARKS_WIKI]
    assert result.degraded and result.degraded[0].startswith("open_meteo:")
    coverage = result.live_context.coverage  # type: ignore[union-attr]
    assert coverage.weather_covered is False
    assert snapshots.rows[result.snapshot_id]["raw"]["open_meteo"] is None


def test_schedule_outage_degrades_weather_coverage_only() -> None:
    result = _collector(Provider(schedule_status=503))[0].collect(now=NOW)

    assert result.created is True
    assert DataSource.OPEN_METEO in result.data_sources
    assert result.live_context.coverage.weather_covered is False  # type: ignore[union-attr]


def test_themeparks_outage_raises_and_stores_nothing() -> None:
    collector, snapshots = _collector(Provider(parks_status=503))

    with pytest.raises(ThemeParksUnavailableError):
        collector.collect(now=NOW)

    assert snapshots.rows == {}


DAPPER_DANS = "1eee22e8-1d0a-4809-a42b-df3ae55c69d5"  # 7 performances in the capture
HAPPILY_EVER_AFTER = "22b78ed9-a692-47cb-b6a4-6d1224ff67e3"  # CLOSED, no showtimes


def _coverage_with_live(live: dict, **collector_kwargs):  # type: ignore[no-untyped-def]
    provider = Provider(live=live)
    collector = SnapshotCollector(
        provider.parks(), provider.weather(), InMemorySnapshotRepository(), InMemoryIdMappingRepository(), **collector_kwargs
    )
    return collector.collect(now=NOW).live_context.coverage  # type: ignore[union-attr]


def test_an_operating_show_without_showtimes_is_a_coverage_gap() -> None:
    live = capture("themeparks_live.json")
    for entity in live["liveData"]:
        if entity["id"] == DAPPER_DANS:
            entity["showtimes"] = []

    coverage = _coverage_with_live(live)

    assert coverage.required_shows_covered is False
    assert any("1 operating show(s) without showtimes" in gap for gap in coverage.coverage_gaps)


def test_a_closed_show_needs_no_showtimes() -> None:
    """Architecture 8.1: only *relevant* shows need showtimes. A CLOSED show can't
    be planned today (rule 1), so its missing showtimes are not a gap."""
    coverage = _coverage_with_live(
        capture("themeparks_live.json"), scheduled_shows=frozenset({HAPPILY_EVER_AFTER})
    )

    assert coverage.required_shows_covered is True


def test_a_curated_show_missing_from_live_is_a_coverage_gap() -> None:
    """Scheduled shows come from curated data, not the payload's kind: a show that
    vanishes from /live still counts as missing (#64 review)."""
    live = capture("themeparks_live.json")
    live["liveData"] = [e for e in live["liveData"] if e["id"] != DAPPER_DANS]

    coverage = _coverage_with_live(live)

    assert coverage.required_shows_covered is False
    assert coverage.required_attractions_covered is False  # it has no status either


def test_no_curated_scheduled_shows_is_reported_as_vacuous() -> None:
    coverage = _coverage_with_live(capture("themeparks_live.json"), scheduled_shows=frozenset())

    assert coverage.required_shows_covered is True
    assert any("show coverage is vacuous" in gap for gap in coverage.coverage_gaps)
