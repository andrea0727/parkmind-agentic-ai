"""
ThemeParks.wiki client — catalog, live waits/status/showtimes, schedules.
API docs: https://api.themeparks.wiki/docs/v1

Implements services.ports.park_data.ParkDataPort structurally (duck-typed,
no inheritance). Public methods never return raw provider JSON/dicts —
"Raw provider schema does not leak into core" (P0-07 Done-when).

Retry/error policy is adapter-level only: bounded retries for transient
failures, distinct exceptions for not-found vs unavailable vs schema
drift. The "degrade to cached snapshot" fallback chain (§43 Failure
Handling) belongs to a later use_cases/load_context, not here.
"""

import logging
import time
from datetime import date, datetime
from typing import Any

import httpx

from parkmind.config.settings import settings
from parkmind.core.contracts import Attraction, AttractionStatus, Park, WaitEstimate
from parkmind.core.contracts.base import PARK_TZ

from .themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    AttractionMetadata,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_CATALOG_ENTITY_TYPES = {"ATTRACTION", "SHOW"}


class ThemeParksClientError(Exception):
    """Base class for all expected ThemeParksClient failures."""


class ThemeParksNotFoundError(ThemeParksClientError):
    """Provider returned 404, or no schedule entry matched the request."""


class ThemeParksUnavailableError(ThemeParksClientError):
    """Transient failure (timeout/connection/5xx/429) survived all retries."""


class ThemeParksSchemaError(ThemeParksClientError):
    """Provider response didn't match the expected shape — unknown status
    value, missing field, ambiguous schedule match, non-JSON body, ...
    Contract drift, never silently swallowed or coerced."""


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
        attraction_metadata: dict[str, AttractionMetadata] = MAGIC_KINGDOM_ATTRACTION_METADATA,
        park_name: str = "Magic Kingdom Park",
        park_outdoor: bool = True,
    ) -> None:
        self._park_id = park_id
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._attraction_metadata = attraction_metadata
        self._park_name = park_name
        self._park_outdoor = park_outdoor
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    def get_catalog(self) -> list[Attraction]:
        payload = self._request(f"/entity/{self._park_id}/children")
        children = payload.get("children", [])

        attractions: list[Attraction] = []
        for entity in children:
            if entity.get("entityType") not in _CATALOG_ENTITY_TYPES:
                continue
            entity_id = entity.get("id")
            metadata = self._attraction_metadata.get(entity_id)
            if metadata is None:
                logger.warning(
                    "ThemeParksClient.get_catalog: no curated metadata for entity "
                    "%s (%r); skipping",
                    entity_id,
                    entity.get("name"),
                )
                continue
            attractions.append(
                Attraction(
                    node_id=entity_id,
                    name=entity["name"],
                    category=metadata["category"],
                    height_restriction_cm=metadata["height_restriction_cm"],
                    typical_wait_minutes=metadata["typical_wait_minutes"],
                    outdoor=metadata["outdoor"],
                )
            )
        return attractions

    def get_schedule(self, on_date: date) -> Park:
        payload = self._request(f"/entity/{self._park_id}/schedule")
        entries = payload.get("schedule", [])

        matches = [
            entry
            for entry in entries
            if entry.get("date") == on_date.isoformat() and entry.get("type") == "OPERATING"
        ]
        if not matches:
            raise ThemeParksNotFoundError(
                f"no OPERATING schedule entry for park {self._park_id} on {on_date.isoformat()}"
            )
        if len(matches) > 1:
            raise ThemeParksSchemaError(
                f"ambiguous schedule: {len(matches)} OPERATING entries for park "
                f"{self._park_id} on {on_date.isoformat()}"
            )

        entry = matches[0]
        try:
            opening_time = datetime.fromisoformat(entry["openingTime"]).astimezone(PARK_TZ)
            closing_time = datetime.fromisoformat(entry["closingTime"]).astimezone(PARK_TZ)
        except (KeyError, ValueError) as exc:
            raise ThemeParksSchemaError(
                f"malformed schedule entry for park {self._park_id} on {on_date.isoformat()}: {exc}"
            ) from exc

        return Park(
            park_id=self._park_id,
            name=self._park_name,
            opening_time=opening_time,
            closing_time=closing_time,
            outdoor=self._park_outdoor,
        )

    def get_live_waits(self, attraction_ids: list[str]) -> dict[str, WaitEstimate]:
        live_entities = self._fetch_live_entities()

        results: dict[str, WaitEstimate] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue

            standby = entity.get("queue", {}).get("STANDBY")
            if standby is None or standby.get("waitTime") is None:
                # No standby queue (Lightning Lane/virtual-queue only, or not
                # tracked) — "standby queues only for planning; other queue
                # types kept raw." We simply never touch non-standby data.
                continue

            status = self._parse_status(entity)
            results[attraction_id] = WaitEstimate(
                attraction_id=attraction_id,
                wait_minutes=standby["waitTime"],
                status=status,
            )
        return results

    def get_attraction_status(self, attraction_ids: list[str]) -> dict[str, AttractionStatus]:
        live_entities = self._fetch_live_entities()

        results: dict[str, AttractionStatus] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue
            results[attraction_id] = self._parse_status(entity)
        return results

    def get_showtimes(self, attraction_ids: list[str]) -> dict[str, list[datetime]]:
        live_entities = self._fetch_live_entities()

        results: dict[str, list[datetime]] = {}
        for attraction_id in attraction_ids:
            entity = live_entities.get(attraction_id)
            if entity is None:
                continue
            showtimes = entity.get("showtimes")
            if not showtimes:
                continue
            try:
                results[attraction_id] = [
                    datetime.fromisoformat(showtime["startTime"]).astimezone(PARK_TZ)
                    for showtime in showtimes
                ]
            except (KeyError, ValueError) as exc:
                raise ThemeParksSchemaError(
                    f"malformed showtimes for entity {attraction_id}: {exc}"
                ) from exc
        return results

    def _parse_status(self, entity: dict[str, Any]) -> AttractionStatus:
        raw_status = entity.get("status")
        try:
            return AttractionStatus(raw_status)
        except ValueError as exc:
            raise ThemeParksSchemaError(
                f"unrecognized status {raw_status!r} for entity {entity.get('id')}"
            ) from exc

    def _fetch_live_entities(self) -> dict[str, dict[str, Any]]:
        payload = self._request(f"/entity/{self._park_id}/live")
        return {
            entity["id"]: entity
            for entity in payload.get("liveData", [])
            if entity.get("id") != self._park_id  # the park itself appears in liveData too
        }

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(1, self._max_retries + 1):
            is_last_attempt = attempt == self._max_retries

            try:
                response = self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if is_last_attempt:
                    raise ThemeParksUnavailableError(
                        f"{path} failed after {self._max_retries} attempts: {exc}"
                    ) from exc
                time.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 404:
                raise ThemeParksNotFoundError(f"{path} returned 404")
            if response.status_code in _RETRYABLE_STATUS_CODES:
                if is_last_attempt:
                    raise ThemeParksUnavailableError(
                        f"{path} returned {response.status_code} after "
                        f"{self._max_retries} attempts"
                    )
                time.sleep(self._backoff_seconds * attempt)
                continue
            if response.status_code >= 400:
                raise ThemeParksClientError(
                    f"{path} returned unexpected status {response.status_code}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise ThemeParksSchemaError(f"{path} returned non-JSON body: {exc}") from exc

        raise AssertionError("unreachable")  # loop always returns or raises
