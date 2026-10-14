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
def provider_and_database(url: str) -> Iterator[None]:
    with provider_down(), database(url):
        yield


def inject(fault: Fault | None) -> contextlib.AbstractContextManager[None]:
    match fault:
        case None:
            return contextlib.nullcontext()
        case Fault.PROVIDER_DOWN:
            return provider_down()
        case Fault.EMPTY_CATALOG_DB:
            return database(EDGE_URL)
        case Fault.PROVIDER_DOWN_EMPTY_DB:
            return provider_and_database(EDGE_URL)
        case Fault.DB_DOWN:
            return database(BAD_URL)
