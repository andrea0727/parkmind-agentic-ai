"""
ThemeParks.wiki client — catalog, live waits/status/showtimes, schedules.
API docs: https://api.themeparks.wiki/docs/v1

Implements services.ports.park_data.ParkDataPort structurally (duck-typed,
no inheritance). Public methods never return raw provider JSON/dicts —
"Raw provider schema does not leak into core" (P0-07 Done-when).

Retry/error policy is adapter-level only (reference pattern for the other
services/clients adapters). The "degrade to cached snapshot" fallback chain
(§43 Failure Handling) belongs to a later use_cases/load_context, not here.
The retry loop and backoff formula themselves live in the shared
`services/clients/_retry.py` helper (issue #56); this module owns only the
policy values and what each outcome means.

- Attempts: `max_retries` is the TOTAL number of attempts (>= 1, validated).
  Backoff is exponential, `backoff_seconds * 2 ** (attempt - 1)`, slept only
  BETWEEN attempts: with the defaults (3 attempts, 0.5s) the delays are 0.5s
  then 1.0s.
- Retried: timeouts, transport errors, and HTTP 429/500/502/503/504. Exhausted
  retries raise ThemeParksUnavailableError.
- Never retried: 404 (ThemeParksNotFoundError) and any other 4xx
  (ThemeParksClientError).
- Redirects (3xx) are NOT followed and NOT retried: they raise
  ThemeParksSchemaError. A redirect means the provider's URL contract changed,
  so we fail closed instead of silently following it (which would hide the
  drift). Retrying is pointless too: the same URL returns the same redirect.
- Unrecognized statuses, malformed entities and non-JSON bodies also raise
  ThemeParksSchemaError; malformed data is never coerced into a best guess.

Parsing lives in `themeparks_normalize` (pure functions over the raw payload,
P0-10) so a stored snapshot can be re-normalized without HTTP. This class only
fetches and delegates; it parses statuses lazily, for the ids it is asked about.
"""

import logging
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Self

import httpx

from parkmind.config.settings import settings
from parkmind.core.contracts import Attraction, AttractionStatus, Park, WaitEstimate

from ._retry import (
    RETRYABLE_STATUS_CODES,
    RetriesExhausted,
    RetryPolicy,
    send_with_retry,
)
from .themeparks_errors import (
    ThemeParksClientError,
    ThemeParksNotFoundError,
    ThemeParksSchemaError,
    ThemeParksUnavailableError,
)
from .themeparks_normalize import (
    check_timezone,
    index_entities,
    parse_catalog,
    parse_schedule,
    parse_showtimes,
    parse_status,
    standby_wait,
)
from .themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    AttractionMetadata,
)

__all__ = [
    "ThemeParksClient",
    "ThemeParksClientError",
    "ThemeParksNotFoundError",
    "ThemeParksSchemaError",
    "ThemeParksUnavailableError",
]

logger = logging.getLogger(__name__)

class ThemeParksClient:
    def __init__(
        self,
        park_id: str,
        *,
        base_url: str = settings.THEMEPARKS_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        attraction_metadata: Mapping[str, AttractionMetadata] | None = None,
        park_name: str = "Magic Kingdom Park",
        park_outdoor: bool = True,
    ) -> None:
        self._park_id = park_id
        self._retry_policy = RetryPolicy(max_attempts=max_retries, backoff_seconds=backoff_seconds)
        # `is None`, not `or`: an intentionally empty table must stay empty.
        self._attraction_metadata: Mapping[str, AttractionMetadata] = (
            MAGIC_KINGDOM_ATTRACTION_METADATA
            if attraction_metadata is None
            else attraction_metadata
        )
        self._park_name = park_name
        self._park_outdoor = park_outdoor
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_catalog(self) -> list[Attraction]:
        payload = self._request(f"/entity/{self._park_id}/children")
        catalog = parse_catalog(payload, self._attraction_metadata)
        for issue in catalog.issues:
            # The port returns attractions only; the collector (P0-11) works on
            # the raw payload and gets these issues as data instead.
            logger.warning(
                "ThemeParksClient.get_catalog: %s %s (%s); skipping",
                issue.kind,
                issue.provider_id,
                issue.detail,
            )
        return catalog.attractions

    def get_schedule(self, on_date: date) -> Park:
        payload = self._request(f"/entity/{self._park_id}/schedule")
        return parse_schedule(
            payload,
            on_date,
            park_id=self._park_id,
            park_name=self._park_name,
            park_outdoor=self._park_outdoor,
        )

    def get_live_waits(self, attraction_ids: list[str]) -> dict[str, WaitEstimate]:
        live_entities = self._fetch_live_entities()

        results: dict[str, WaitEstimate] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue

            wait = standby_wait(entity)
            if wait is None:
                # No standby queue (Lightning Lane/virtual-queue only, or not
                # tracked) — "standby queues only for planning; other queue
                # types kept raw." We simply never touch non-standby data.
                continue

            results[attraction_id] = WaitEstimate(
                attraction_id=attraction_id,
                wait_minutes=wait,
                status=parse_status(entity),
            )
        return results

    def get_attraction_status(self, attraction_ids: list[str]) -> dict[str, AttractionStatus]:
        live_entities = self._fetch_live_entities()

        results: dict[str, AttractionStatus] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue
            results[attraction_id] = parse_status(entity)
        return results

    def get_showtimes(self, attraction_ids: list[str]) -> dict[str, list[datetime]]:
        live_entities = self._fetch_live_entities()

        results: dict[str, list[datetime]] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue
            showtimes = parse_showtimes(entity)
            if showtimes:
                results[attraction_id] = showtimes
        return results

    def _fetch_live_entities(self) -> dict[str, Mapping[str, Any]]:
        payload = self._request(f"/entity/{self._park_id}/live")
        check_timezone(payload)
        entities, issues = index_entities(payload.get("liveData", []))
        for issue in issues:
            logger.warning(
                "ThemeParksClient: %s %s (%s); excluded", issue.kind, issue.provider_id, issue.detail
            )
        entities.pop(self._park_id, None)  # the park itself appears in liveData too
        return entities

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = send_with_retry(
                lambda: self._client.get(path, params=params),
                policy=self._retry_policy,
            )
        except RetriesExhausted as exc:
            raise ThemeParksUnavailableError(
                f"{path} failed after {exc.attempts} attempts: {exc.last_error}"
            ) from exc

        if response.status_code == 404:
            raise ThemeParksNotFoundError(f"{path} returned 404")
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise ThemeParksUnavailableError(
                f"{path} returned {response.status_code} after "
                f"{self._retry_policy.max_attempts} attempts"
            )
        if 300 <= response.status_code < 400:
            # Policy: fail closed. httpx.Client() defaults to
            # follow_redirects=False, and we keep it: a 3xx means the
            # provider's URL contract changed. Not retried either: the
            # same URL would return the same redirect (see module docstring).
            raise ThemeParksSchemaError(
                f"{path} returned unhandled redirect (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            raise ThemeParksClientError(
                f"{path} returned unexpected status {response.status_code}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ThemeParksSchemaError(f"{path} returned non-JSON body: {exc}") from exc
