"""``ContextDataPort`` over ``parkmind-mcp`` (P0-24): LOAD CONTEXT through the MCP client.

Each read is one ``data.*`` / ``knowledge.*`` tool call; the structured results
are validated back into section 33 contracts, so nothing provider- or
transport-shaped crosses the port. The MCP SDK is async and LOAD CONTEXT is
not: every call runs on its own event loop in a worker thread (an ``anyio``
blocking portal), which works under ``graph.invoke`` and inside a caller that
already runs a loop.

Failures are split in two. A transport that fails -- unreachable server,
timeout, protocol error, malformed answer -- raises ``ContextTransportError``,
and LOAD CONTEXT falls back to the in-process adapter. A tool that answers
``UNAVAILABLE`` or ``NOT_FOUND`` (no snapshot, no schedule) is a domain answer,
not a broken transport: it raises ``ContextDataUnavailableError``.

``server`` is anything ``mcp.Client`` connects to: the streamable-HTTP URL of
``scripts/run_mcp_server.py --http`` in the demo, an in-process ``MCPServer`` in
tests.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from anyio.from_thread import start_blocking_portal
from mcp import Client
from pydantic import ValidationError

from parkmind.core.contracts import (
    AccessibilityCheck,
    Attraction,
    AttractionStatus,
    Park,
    RideRestriction,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.ports import (
    ContextDataUnavailableError,
    ContextTransportError,
    FromSnapshotRef,
    SnapshotRef,
)

_DOMAIN_ERRORS = {"UNAVAILABLE", "NOT_FOUND"}
_ACCESSIBILITY_BATCH = 100


class McpContextData:
    transport = "mcp"

    def __init__(self, server: Any, *, timeout_seconds: float = 15.0) -> None:
        self._server = server
        self._timeout = timeout_seconds

    # -- ContextDataPort ---------------------------------------------------------------

    def catalog(self) -> list[Attraction]:
        data, _ = self._call("data.get_attraction_info", {})
        return self._parse(
            lambda: [Attraction.model_validate(a) for a in data["attractions"]]
        )

    def schedule(self, on_date: date) -> Park:
        data, _ = self._call("data.get_schedule", {"service_date": on_date.isoformat()})
        return self._parse(lambda: Park.model_validate(data["park"]))

    def live_waits(self) -> FromSnapshotRef[list[WaitEstimate]]:
        data, provenance = self._call("data.get_live_waits", {})
        waits = self._parse(
            lambda: [WaitEstimate.model_validate(w) for w in data["waits"]]
        )
        return FromSnapshotRef(waits, self._snapshot(provenance))

    def attraction_statuses(self) -> FromSnapshotRef[dict[str, AttractionStatus]]:
        data, provenance = self._call("data.get_attraction_status", {})
        statuses = self._parse(
            lambda: {k: AttractionStatus(v) for k, v in data["statuses"].items()}
        )
        return FromSnapshotRef(statuses, self._snapshot(provenance))

    def showtimes(self) -> FromSnapshotRef[dict[str, list[datetime]]]:
        data, provenance = self._call("data.get_showtimes", {})
        times = self._parse(
            lambda: {
                k: [datetime.fromisoformat(t) for t in v]
                for k, v in data["showtimes"].items()
            }
        )
        return FromSnapshotRef(times, self._snapshot(provenance))

    def weather(
        self, start: datetime, end: datetime
    ) -> FromSnapshotRef[list[WeatherHour]]:
        data, provenance = self._call(
            "data.get_weather", {"start": start.isoformat(), "end": end.isoformat()}
        )
        hours = self._parse(
            lambda: [WeatherHour.model_validate(h) for h in data["hours"]]
        )
        return FromSnapshotRef(hours, self._snapshot(provenance))

    def check_accessibility(
        self,
        guest_id: str,
        flags: frozenset[RideRestriction],
        attraction_ids: Sequence[str],
    ) -> list[AccessibilityCheck]:
        checks: list[AccessibilityCheck] = []
        for start in range(0, len(attraction_ids), _ACCESSIBILITY_BATCH):
            arguments = {
                "attraction_ids": list(
                    attraction_ids[start : start + _ACCESSIBILITY_BATCH]
                ),
                "flags": sorted(f.value for f in flags),
                "guest_id": guest_id,
            }
            data, _ = self._call("knowledge.check_accessibility", arguments)
            checks += self._parse(
                lambda d=data: [
                    AccessibilityCheck.model_validate(c) for c in d["checks"]
                ]
            )
        return checks

    # -- transport ---------------------------------------------------------------------

    def _call(
        self, tool: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            with start_blocking_portal() as portal:
                result = portal.call(self._call_async, tool, arguments)
        except (ContextTransportError, ContextDataUnavailableError):
            raise
        except (
            Exception
        ) as exc:  # the SDK and httpx raise many types; all mean "transport"
            raise ContextTransportError(f"{tool}: {type(exc).__name__}: {exc}") from exc
        content = result.structured_content
        if result.is_error:
            error = (
                (content or {}).get("error", {}) if isinstance(content, dict) else {}
            )
            if error.get("code") in _DOMAIN_ERRORS:
                raise ContextDataUnavailableError(f"{tool}: {error.get('message', '')}")
            raise ContextTransportError(
                f"{tool} failed: {error.get('code', 'unstructured error')}"
            )
        if not isinstance(content, dict) or "data" not in content:
            raise ContextTransportError(f"{tool} returned no structured result")
        return content["data"], content.get("provenance") or {}

    async def _call_async(self, tool: str, arguments: dict[str, Any]) -> Any:
        async with Client(self._server, read_timeout_seconds=self._timeout) as client:
            return await client.call_tool(tool, arguments)

    @staticmethod
    def _parse(build: Any) -> Any:
        try:
            return build()
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ContextTransportError(
                f"malformed tool result: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def _snapshot(provenance: dict[str, Any]) -> SnapshotRef:
        try:
            return SnapshotRef(
                snapshot_id=str(provenance["snapshot_id"]),
                retrieved_at=datetime.fromisoformat(provenance["retrieved_at"]),
                origin=str(provenance.get("strategy") or "snapshot"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextTransportError(
                "a snapshot-backed result named no snapshot"
            ) from exc
