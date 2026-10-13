"""Request and response bodies of the HTTP API (the OpenAPI contract)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from parkmind.core.contracts import EventType, RejectionReason
from parkmind.core.conversation import TurnStatus


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until the token expires.")


class SessionOut(BaseModel):
    thread_id: str = Field(description="Pass as `?thread_id=` on every other call.")


class MessageIn(_Body):
    text: str = Field(
        min_length=1, max_length=4000, description="What the guests said."
    )
    lang: Literal["es", "en", "pt", "fr"] = Field(
        default="en",
        description=(
            "UI language. The chat answers in the language the guests write (English or "
            "Spanish, detected from the text); this is only the fallback when a first "
            "message says too little to tell."
        ),
    )


class ConstraintsConfirmIn(_Body):
    confirmed: bool
    consent: bool = Field(
        default=False, description="Consent to store accessibility data."
    )
    retention_policy: Literal["session_only", "persisted"] = "session_only"
    correction: str | None = Field(default=None, max_length=2000)


class DecisionIn(_Body):
    reason: RejectionReason | None = Field(
        default=None, description="Why the proposal was rejected; ignored on approve."
    )


class EventIn(_Body):
    """A guest-reported event. Severity and replan need are decided by the planner, never here."""

    type: EventType
    attraction_id: str | None = None
    guest_id: str | None = None
    location_node_id: str | None = None
    delta_minutes: float | None = Field(default=None, ge=0)
    minutes_behind: float | None = Field(default=None, ge=0)
    affected_hours: list[datetime] | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class EventsConfirmIn(_Body):
    confirmed: bool


class StopOut(BaseModel):
    stop_id: str = Field(
        description="`node_id` for attractions, `node_id:KIND` otherwise."
    )
    node_id: str
    kind: str
    title: str
    land: str | None
    arrival: datetime
    departure: datetime
    wait_minutes: float
    walking_minutes: float
    served_guests: list[str]


class PlanOut(BaseModel):
    plan_id: str
    version: int
    stops: list[StopOut]
    total_wait_minutes: float
    total_walking_minutes: float
    unmet_must_do: list[str]
    snapshot_id: str
    retrieved_at: datetime


class DiffEntry(BaseModel):
    stop_id: str
    title: str


class ChangedEntry(DiffEntry):
    changes: dict[str, list[Any]] = Field(description="field -> [before, after]")


class DiffOut(BaseModel):
    removed: list[DiffEntry]
    added: list[DiffEntry]
    moved: list[DiffEntry]
    modified: list[ChangedEntry]


class MetricsOut(BaseModel):
    wait_minutes_before: float | None
    wait_minutes_after: float
    walking_minutes_before: float | None
    walking_minutes_after: float


class ProvenanceOut(BaseModel):
    snapshot_id: str
    retrieved_at: datetime
    forecast_strategy: str
    optimizer_strategy: str
    constraints_version: int


class ProposalOut(BaseModel):
    proposal_id: str
    base_plan_id: str
    candidate_plan_id: str
    trigger: str = Field(description="Why this plan is proposed.")
    explanation: str
    diff: DiffOut
    metrics: MetricsOut
    provenance: ProvenanceOut
    plan: PlanOut = Field(description="The candidate plan.")


class ConfirmationOut(BaseModel):
    pending: list[str]
    echo: list[str] = Field(
        description="The constraints, in plain language, to read back."
    )
    accessibility_guests: list[str]
    consent_required: bool


class HeldEventOut(BaseModel):
    event_id: str
    type: EventType
    attraction_id: str | None
    guest_id: str | None
    confidence: float


class TurnOut(BaseModel):
    thread_id: str
    status: TurnStatus
    messages: list[str]
    missing: list[str]
    question: str | None
    confirmation: ConfirmationOut | None
    proposal: ProposalOut | None
    held_events: list[HeldEventOut]
    error: str | None


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    auth_required: bool = True


class ProblemOut(BaseModel):
    detail: str
