"""Unit tests for ThemeParksClient — all network calls are mocked via
httpx.MockTransport, backed by real-shaped fixtures in
tests/fixtures/themeparks/. No test opens a real socket.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from parkmind.core.contracts import (
    Attraction,
    AttractionCategory,
    AttractionStatus,
    Park,
    WaitEstimate,
)
from parkmind.services.clients.themeparks_client import (
    ThemeParksClient,
    ThemeParksClientError,
    ThemeParksNotFoundError,
    ThemeParksSchemaError,
    ThemeParksUnavailableError,
)
from parkmind.services.ports import ParkDataPort

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "themeparks"
PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _accepts_port(port: ParkDataPort) -> None:
    """mypy-only check that ThemeParksClient satisfies ParkDataPort structurally."""


def _fixture_transport(routes: dict[str, dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _client(routes: dict[str, dict], **kwargs) -> ThemeParksClient:
    client = ThemeParksClient(
        park_id=PARK_ID,
        base_url="https://testserver",  # no path prefix, unlike the real THEMEPARKS_BASE_URL
        transport=_fixture_transport(routes),
        backoff_seconds=0,
        **kwargs,
    )
    _accepts_port(client)
    return client


# ============================================================================
# CATALOG
# ============================================================================


class TestGetCatalog:
    def test_valid_catalog_filters_and_normalizes(self):
        """ATTRACTION/SHOW kept, RESTAURANT/PARK dropped, unknown metadata skipped."""
        client = _client(
            {f"/entity/{PARK_ID}/children": _load_fixture("children_magic_kingdom.json")}
        )
        catalog = client.get_catalog()

        assert all(isinstance(a, Attraction) for a in catalog)
        node_ids = {a.node_id for a in catalog}
        # Kept: the 4 curated ATTRACTION entries.
        assert "b2260923-9315-40fd-9c6b-44dd811dbe64" in node_ids  # Space Mountain
        assert "de3309ca-97d5-4211-bffe-739fed47e92f" in node_ids  # Big Thunder
        assert "5a43d1a7-ad53-4d25-abfe-25625f0da304" in node_ids  # TRON
        assert "796b0a25-c51e-456e-9bb8-50a324e301b3" in node_ids  # Jungle Cruise
        # Dropped: RESTAURANT, self-referencing PARK, and a SHOW (not yet curated).
        assert "c4b64a31-e855-46a9-9d1a-aa13c99e88af" not in node_ids
        assert PARK_ID not in node_ids
        assert "4c31b3ad-5dc9-437f-ac1a-0fdff36a2818" not in node_ids
        # Dropped: an ATTRACTION-type entity missing from the metadata table.
        assert "unknown-attraction-not-in-metadata-table" not in node_ids

    def test_catalog_entry_fields_match_curated_metadata(self):
        client = _client(
            {f"/entity/{PARK_ID}/children": _load_fixture("children_magic_kingdom.json")}
        )
        catalog = client.get_catalog()
        space_mountain = next(a for a in catalog if a.node_id == "b2260923-9315-40fd-9c6b-44dd811dbe64")

        assert space_mountain.name == "Space Mountain"
        assert space_mountain.height_restriction_cm == 112
        assert space_mountain.outdoor is False

    def test_explicit_empty_metadata_table_is_not_replaced_by_default(self):
        """`{}` is falsy; it must not silently fall back to the Magic Kingdom table."""
        client = _client(
            {f"/entity/{PARK_ID}/children": _load_fixture("children_magic_kingdom.json")},
            attraction_metadata={},
        )
        assert client.get_catalog() == []

    def test_custom_metadata_table_is_honored(self):
        space_mountain_id = "b2260923-9315-40fd-9c6b-44dd811dbe64"
        custom = {
            space_mountain_id: {
                "category": AttractionCategory.THRILL,
                "height_restriction_cm": 100,
                "typical_wait_minutes": 5,
                "outdoor": True,
            }
        }
        client = _client(
            {f"/entity/{PARK_ID}/children": _load_fixture("children_magic_kingdom.json")},
            attraction_metadata=custom,
        )
        catalog = client.get_catalog()

        assert [a.node_id for a in catalog] == [space_mountain_id]
        assert catalog[0].height_restriction_cm == 100

    def test_malformed_entity_raises_schema_error(self):
        """A curated ATTRACTION entity missing "name" raises ThemeParksSchemaError,
        not a raw KeyError."""
        client = _client(
            {
                f"/entity/{PARK_ID}/children": {
                    "children": [
                        {
                            "id": "b2260923-9315-40fd-9c6b-44dd811dbe64",  # Space Mountain
                            "entityType": "ATTRACTION",
                        }
                    ]
                }
            }
        )
        with pytest.raises(ThemeParksSchemaError):
            client.get_catalog()


# ============================================================================
# LIVE WAITS
# ============================================================================


class TestGetLiveWaits:
    def test_standby_wait_normalized(self):
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        waits = client.get_live_waits(["b2260923-9315-40fd-9c6b-44dd811dbe64"])

        estimate = waits["b2260923-9315-40fd-9c6b-44dd811dbe64"]
        assert isinstance(estimate, WaitEstimate)
        assert estimate.wait_minutes == 60
        assert estimate.status == AttractionStatus.OPERATING

    def test_non_standby_only_attraction_omitted(self):
        """Jungle Cruise has only a RETURN_TIME queue in the fixture — no STANDBY key at all."""
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        waits = client.get_live_waits(["796b0a25-c51e-456e-9bb8-50a324e301b3"])

        assert "796b0a25-c51e-456e-9bb8-50a324e301b3" not in waits

    def test_null_waittime_omitted(self):
        """The self-referencing PARK entity has queue.STANDBY.waitTime=null; also filtered
        out entirely before this point since it shares the park's own id."""
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        waits = client.get_live_waits([PARK_ID])

        assert waits == {}

    def test_unrequested_id_silently_omitted(self):
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        waits = client.get_live_waits(["does-not-exist"])

        assert waits == {}

    def test_unknown_status_raises_schema_error(self):
        client = _client(
            {
                f"/entity/{PARK_ID}/live": _load_fixture(
                    "live_magic_kingdom_unknown_status.json"
                )
            }
        )
        with pytest.raises(ThemeParksSchemaError):
            client.get_live_waits(["b2260923-9315-40fd-9c6b-44dd811dbe64"])


# ============================================================================
# ATTRACTION STATUS
# ============================================================================


class TestGetAttractionStatus:
    @pytest.mark.parametrize(
        ("node_id", "expected"),
        [
            ("b2260923-9315-40fd-9c6b-44dd811dbe64", AttractionStatus.OPERATING),
            ("de3309ca-97d5-4211-bffe-739fed47e92f", AttractionStatus.DOWN),
            ("5a43d1a7-ad53-4d25-abfe-25625f0da304", AttractionStatus.REFURBISHMENT),
            ("796b0a25-c51e-456e-9bb8-50a324e301b3", AttractionStatus.CLOSED),
        ],
    )
    def test_all_canonical_statuses(self, node_id, expected):
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        statuses = client.get_attraction_status([node_id])
        assert statuses[node_id] == expected


# ============================================================================
# SHOWTIMES
# ============================================================================


class TestGetShowtimes:
    def test_showtimes_are_timezone_aware(self):
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        showtimes = client.get_showtimes(["4c31b3ad-5dc9-437f-ac1a-0fdff36a2818"])

        times = showtimes["4c31b3ad-5dc9-437f-ac1a-0fdff36a2818"]
        assert len(times) == 2
        assert all(t.tzinfo is not None for t in times)

    def test_entity_without_showtimes_omitted(self):
        client = _client({f"/entity/{PARK_ID}/live": _load_fixture("live_magic_kingdom.json")})
        showtimes = client.get_showtimes(["b2260923-9315-40fd-9c6b-44dd811dbe64"])
        assert showtimes == {}


# ============================================================================
# SCHEDULE
# ============================================================================


class TestGetSchedule:
    def test_operating_entry_returns_park(self):
        client = _client(
            {
                f"/entity/{PARK_ID}/schedule": _load_fixture(
                    "schedule_magic_kingdom_2026-09-16.json"
                )
            }
        )
        park = client.get_schedule(date(2026, 9, 16))

        assert isinstance(park, Park)
        assert park.park_id == PARK_ID
        assert park.closing_time > park.opening_time
        assert park.opening_time.tzinfo is not None
        # TICKETED_EVENT (early entry) must NOT be selected over OPERATING.
        assert park.opening_time.hour == 9

    def test_no_match_raises_not_found(self):
        client = _client(
            {f"/entity/{PARK_ID}/schedule": _load_fixture("schedule_magic_kingdom_no_match.json")}
        )
        with pytest.raises(ThemeParksNotFoundError):
            client.get_schedule(date(2026, 9, 16))


# ============================================================================
# RETRIES / ERRORS
# ============================================================================


class TestRetriesAndErrors:
    def test_transient_failure_then_success(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] < 2:
                return httpx.Response(503)
            return httpx.Response(200, json=_load_fixture("schedule_magic_kingdom_2026-09-16.json"))

        client = ThemeParksClient(
            park_id=PARK_ID, transport=httpx.MockTransport(handler), backoff_seconds=0
        )
        park = client.get_schedule(date(2026, 9, 16))

        assert isinstance(park, Park)
        assert calls["count"] == 2

    def test_persistent_failure_raises_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        client = ThemeParksClient(
            park_id=PARK_ID,
            transport=httpx.MockTransport(handler),
            backoff_seconds=0,
            max_retries=3,
        )
        with pytest.raises(ThemeParksUnavailableError):
            client.get_schedule(date(2026, 9, 16))

    def test_404_is_not_retried(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(404)

        client = ThemeParksClient(
            park_id=PARK_ID, transport=httpx.MockTransport(handler), backoff_seconds=0
        )
        with pytest.raises(ThemeParksNotFoundError):
            client.get_schedule(date(2026, 9, 16))
        assert calls["count"] == 1

    def test_unexpected_4xx_is_not_retried(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(400)

        client = ThemeParksClient(
            park_id=PARK_ID, transport=httpx.MockTransport(handler), backoff_seconds=0
        )
        with pytest.raises(ThemeParksClientError):
            client.get_schedule(date(2026, 9, 16))
        assert calls["count"] == 1

    def test_max_retries_zero_rejected_at_construction(self):
        with pytest.raises(ValueError, match="max_retries"):
            ThemeParksClient(park_id=PARK_ID, max_retries=0)

    def test_redirect_raises_schema_error_not_retried(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(301, headers={"location": "/moved"})

        client = ThemeParksClient(
            park_id=PARK_ID, transport=httpx.MockTransport(handler), backoff_seconds=0
        )
        with pytest.raises(ThemeParksSchemaError):
            client.get_schedule(date(2026, 9, 16))
        assert calls["count"] == 1

    def test_context_manager_closes_underlying_client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_load_fixture("schedule_magic_kingdom_2026-09-16.json"))

        with ThemeParksClient(
            park_id=PARK_ID, transport=httpx.MockTransport(handler), backoff_seconds=0
        ) as client:
            park = client.get_schedule(date(2026, 9, 16))
            assert isinstance(park, Park)
        assert client._client.is_closed
