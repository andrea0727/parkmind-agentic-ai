"""Failures injected around a live run, as context managers."""

import contextlib
from collections.abc import Iterator
from typing import Any

from e2e.kit.env import BAD_URL, EDGE_URL
from e2e.model import Fault
from parkmind.config.settings import settings
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_errors import ThemeParksUnavailableError

_PROVIDER_CALLS = (
    "get_catalog",
    "get_schedule",
    "get_live_waits",
    "get_attraction_status",
    "get_showtimes",
    "fetch_live_payload",
    "fetch_schedule_payload",
)


@contextlib.contextmanager
def provider_down() -> Iterator[None]:
    saved: dict[str, Any] = {}

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise ThemeParksUnavailableError("simulated outage")

    for name in _PROVIDER_CALLS:
        saved[name] = getattr(ThemeParksClient, name)
        setattr(ThemeParksClient, name, unavailable)
    try:
        yield
    finally:
        for name, original in saved.items():
            setattr(ThemeParksClient, name, original)


@contextlib.contextmanager
def database(url: str) -> Iterator[None]:
    previous = settings.DATABASE_URL
    settings.DATABASE_URL = url
    try:
        yield
    finally:
        settings.DATABASE_URL = previous


@contextlib.contextmanager
def empty_database(url: str) -> Iterator[None]:
    """The edge database starts without catalog rows, so a previous run cannot fill it."""
    if url == settings.DATABASE_URL:
        raise RuntimeError("refusing to empty the main database")
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("delete from attractions")
    with database(url):
        yield


@contextlib.contextmanager
def provider_and_database(url: str) -> Iterator[None]:
    with provider_down(), empty_database(url):
        yield


def _park_is_closed(entities: list[dict[str, Any]]) -> bool:
    rides = [e for e in entities if e.get("entityType") == "ATTRACTION"]
    operating = [e for e in rides if e.get("status") == "OPERATING"]
    return len(operating) < len(rides) / 2


def _drop_closed_snapshot(original: Any) -> bool:
    """A snapshot is reused for its whole collection window, so one taken while the park was closed would hide the open feed."""
    from e2e.kit.env import e2e_now
    from parkmind.services.use_cases.collect_snapshot import snapshot_id_for
    from parkmind.services.use_cases.planning_deps import MAGIC_KINGDOM_PARK_ID

    with ThemeParksClient(MAGIC_KINGDOM_PARK_ID) as client:
        closed = _park_is_closed(original(client)["liveData"])
    if not closed:
        return False
    import psycopg

    snapshot_id = snapshot_id_for(MAGIC_KINGDOM_PARK_ID, e2e_now())
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as connection:
        connection.execute("delete from snapshots where snapshot_id = %s", (snapshot_id,))
    return True


def _collect_open_snapshot() -> None:
    from e2e.kit.env import e2e_now
    from parkmind.services.use_cases.planning_deps import default_planning_deps

    with default_planning_deps() as deps:
        assert deps.collector is not None
        deps.collector.collect(now=e2e_now())


@contextlib.contextmanager
def park_open() -> Iterator[None]:
    """Outside park hours the live feed reports every ride closed; show it open with a standby wait.

    The schedule is untouched, and a feed that already shows the park open is passed through.
    The window's snapshot is taken again through the open feed, so scenarios that run with the
    provider down still find a stored snapshot of an open park.
    """
    original = ThemeParksClient.fetch_live_payload
    was_closed = _drop_closed_snapshot(original)

    def open_feed(self: ThemeParksClient) -> dict[str, Any]:
        payload = original(self)
        entities = payload.get("liveData", [])
        if not _park_is_closed(entities):
            return payload
        for entity in entities:
            if entity.get("entityType") != "ATTRACTION":
                continue
            if entity.get("status") == "CLOSED":
                entity["status"] = "OPERATING"
            standby = (entity.get("queue") or {}).get("STANDBY")
            if standby is not None and standby.get("waitTime") is None:
                standby["waitTime"] = 20
        return payload

    ThemeParksClient.fetch_live_payload = open_feed  # type: ignore[method-assign]
    try:
        if was_closed:
            _collect_open_snapshot()
        yield
    finally:
        ThemeParksClient.fetch_live_payload = original  # type: ignore[method-assign]


def inject(fault: Fault | None) -> contextlib.AbstractContextManager[None]:
    match fault:
        case None:
            return contextlib.nullcontext()
        case Fault.PROVIDER_DOWN:
            return provider_down()
        case Fault.EMPTY_CATALOG_DB:
            return empty_database(EDGE_URL)
        case Fault.PROVIDER_DOWN_EMPTY_DB:
            return provider_and_database(EDGE_URL)
        case Fault.DB_DOWN:
            return database(BAD_URL)
