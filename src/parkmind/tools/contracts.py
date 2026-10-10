"""The common MCP contract (Architecture section 32): every tool returns
``{data, provenance}``; every anticipated failure is a typed ``ToolErrorPayload``.

These are transport types of the published capability boundary (section 27
[C23]), not domain contracts: ``data`` holds section 33 contracts (or small
response models made of them) and ``provenance`` answers "where did this number
come from?" without the LLM's memory. The full plan-level ``Provenance`` stays
in section 33; tools that return a plan carry it inside ``data``.
"""

from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from parkmind.core.contracts import ParkMindBaseModel

DataT = TypeVar("DataT", bound=BaseModel)


class ToolProvenance(ParkMindBaseModel):
    """Where a tool's answer came from (section 32).

    Snapshot-backed answers carry the snapshot id and its age (P0-25); a §43
    fallback that answered instead of the primary source names itself in
    ``degraded``.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    """Who produced the data: ``ThemeParks.wiki``, ``Open-Meteo``, ``park_safety_notice``..."""
    snapshot_id: str | None = None
    retrieved_at: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    stale: bool | None = None
    """``True`` when the snapshot is older than the freshness window rule 11 uses."""
    strategy: str | None = None
    """Forecast or retrieval strategy, e.g. ``api_forecast`` or ``semantic``."""
    version: str | None = None
    """Corpus, normalizer or model version the answer depends on."""
    degraded: str | None = None
    """Why a fallback answered (section 43), e.g. ``embedding_model_unavailable``."""


class ToolResult(BaseModel, Generic[DataT]):
    """The section 32 envelope every tool returns on success."""

    model_config = ConfigDict(extra="forbid")

    data: DataT
    provenance: ToolProvenance


class ToolErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"


class ToolErrorPayload(BaseModel):
    """A structured tool failure (P0-24 Done-when: "Errors are structured").

    ``message`` and ``details`` never echo argument values: a caller's values
    may be personal data, so details name fields, not their contents.
    """

    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str
    retryable: bool
    details: dict[str, list[str]] = Field(default_factory=dict)
