"""ContextDataPort -- the data/knowledge reads LOAD CONTEXT can make through a transport (P0-24).

Architecture section 27 [C23]: in the demo configuration the context loader
consumes ``data.*`` and ``knowledge.*`` **through the MCP client**, with the
in-process loader as the fallback "selected by configuration through the same
port". This is that port: one method per read-only capability the loader
needs, answering in section 33 contracts. Two adapters implement it -- the
in-process use cases (``use_cases/tool_context.py``) and the MCP client
(``clients/mcp/context_client.py``) -- so switching transport changes nothing
above it.

Snapshot-backed answers carry the snapshot they came from, so the loader can
check that one context is not stitched from two snapshots. Accessibility is
asked with a guest's *derived* flags (section 30's input): the requirements
themselves never leave the loader's process [C19].
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Generic, Protocol, TypeVar

from parkmind.core.contracts import (
    AccessibilityCheck,
    Attraction,
    AttractionStatus,
    Park,
    RideRestriction,
    WaitEstimate,
    WeatherHour,
)

T = TypeVar("T")


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    retrieved_at: datetime
    origin: str
    """``live`` (collected for this call's window) or ``snapshot`` (the stored fallback)."""


@dataclass(frozen=True)
class FromSnapshotRef(Generic[T]):
    value: T
    snapshot: SnapshotRef


class ContextDataPort(Protocol):
    @property
    def transport(self) -> str:
        """``in_process`` or ``mcp`` -- recorded in the context's tool trace."""
        ...

    def catalog(self) -> list[Attraction]: ...

    def schedule(self, on_date: date) -> Park: ...

    def live_waits(self) -> FromSnapshotRef[list[WaitEstimate]]: ...

    def attraction_statuses(self) -> FromSnapshotRef[dict[str, AttractionStatus]]: ...

    def showtimes(self) -> FromSnapshotRef[dict[str, list[datetime]]]: ...

    def weather(
        self, start: datetime, end: datetime
    ) -> FromSnapshotRef[list[WeatherHour]]: ...

    def check_accessibility(
        self,
        guest_id: str,
        flags: frozenset[RideRestriction],
        attraction_ids: Sequence[str],
    ) -> list[AccessibilityCheck]: ...
