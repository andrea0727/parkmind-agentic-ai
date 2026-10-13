"""FastAPI application (P0-35): thin routes over a ``Conversation``.

A route validates its body, makes one call on the conversation and presents the result. It
never plans, checks or decides anything; the planner's gates are behind the conversation.
"""

import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from parkmind.api.guard import MultipleWorkersError, ensure_single_worker
from parkmind.api.presenters import Presenter
from parkmind.api.schemas import (
    ConstraintsConfirmIn,
    DecisionIn,
    EventIn,
    EventsConfirmIn,
    HealthOut,
    MessageIn,
    PlanOut,
    ProblemOut,
    ProposalOut,
    SessionOut,
    TurnOut,
)
from parkmind.core.contracts import PARK_TZ, ApprovalStatus, Event, EventSource
from parkmind.core.conversation import (
    Conversation,
    ConversationError,
    HardConstraintAnswer,
    NotExpectedError,
    StaleProposalError,
    UnknownThreadError,
)
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError

DESCRIPTION = """
Talk to the ParkMind planner for one party at Magic Kingdom.

1. `POST /api/session` returns a `thread_id`; pass it as `?thread_id=` on every other call.
2. `POST /api/message` with what the guests want. The answer's `status` says what the planner
   needs next: an answer (`needs_answer`), confirmation of hard constraints
   (`needs_confirmation`), or a decision on a proposal (`needs_approval`).
3. `GET /api/proposal` is what the guests are being asked to approve (204 when nothing is).
4. `POST /api/proposal/{id}/approve|reject` is the only way a plan becomes the active plan.
"""

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemOut, "description": "Unknown session."},
    409: {"model": ProblemOut, "description": "The planner is not waiting for that."},
    503: {"model": ProblemOut, "description": "Park data or routing unavailable."},
}

ThreadId = Annotated[
    str, Query(min_length=1, max_length=64, description="From POST /api/session.")
]


def _park_now() -> datetime:
    return datetime.now(PARK_TZ)


def _new_event_id() -> str:
    return f"ev_{uuid4().hex[:12]}"


def _problem(status: int, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": str(exc)})


def create_app(
    conversation: Conversation,
    describe: DescribePlanUseCase | None = None,
    *,
    clock: Callable[[], datetime] = _park_now,
    new_event_id: Callable[[], str] = _new_event_id,
    ui_dir: Path | None = None,
    cors_origins: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ensure_single_worker(os.environ if env is None else env, argv)
        yield

    app = FastAPI(
        title="ParkMind API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "conversation", "description": "Talking to the planner."},
            {"name": "plan", "description": "Proposals and the active plan."},
            {"name": "events", "description": "Things that happened in the park."},
        ],
    )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    present = Presenter(describe or DescribePlanUseCase())

    @app.exception_handler(UnknownThreadError)
    async def _unknown(_: Request, exc: UnknownThreadError) -> JSONResponse:
        return _problem(404, exc)

    @app.exception_handler(NotExpectedError)
    async def _not_expected(_: Request, exc: NotExpectedError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(StaleProposalError)
    async def _stale(_: Request, exc: StaleProposalError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(ConversationError)
    async def _conversation(_: Request, exc: ConversationError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(PlanningUnavailableError)
    async def _unavailable(_: Request, exc: PlanningUnavailableError) -> JSONResponse:
        return _problem(503, exc)

    @app.get("/api/health", tags=["conversation"], response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut()

    @app.post(
        "/api/session",
        tags=["conversation"],
        response_model=SessionOut,
        status_code=201,
    )
    def start_session() -> SessionOut:
        return SessionOut(thread_id=conversation.start())

    @app.post(
        "/api/message", tags=["conversation"], response_model=TurnOut, responses=_ERRORS
    )
    def say(body: MessageIn, thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.say(thread_id, body.text))

    @app.post(
        "/api/constraints/confirm",
        tags=["conversation"],
        response_model=TurnOut,
        responses=_ERRORS,
    )
    def confirm_constraints(body: ConstraintsConfirmIn, thread_id: ThreadId) -> TurnOut:
        answer = HardConstraintAnswer(
            confirmed=body.confirmed,
            consent=body.consent,
            retention_policy=body.retention_policy,
            correction=body.correction,
        )
        return present.turn(conversation.confirm_constraints(thread_id, answer))

    @app.get(
        "/api/proposal",
        tags=["plan"],
        response_model=ProposalOut,
        responses={
            **_ERRORS,
            204: {"description": "Nothing is waiting for a decision."},
        },
    )
    def pending_proposal(thread_id: ThreadId) -> Response | ProposalOut:
        pending = conversation.pending_proposal(thread_id)
        if pending is None:
            return Response(status_code=204)
        return present.proposal(*pending)

    @app.post(
        "/api/proposal/{proposal_id}/{decision}",
        tags=["plan"],
        response_model=TurnOut,
        responses=_ERRORS,
    )
    def decide(
        proposal_id: str,
        decision: Literal["approve", "reject"],
        thread_id: ThreadId,
        body: DecisionIn | None = None,
    ) -> TurnOut:
        status = (
            ApprovalStatus.APPROVED
            if decision == "approve"
            else ApprovalStatus.REJECTED
        )
        reason = (
            body.reason
            if body is not None and status == ApprovalStatus.REJECTED
            else None
        )
        return present.turn(conversation.decide(thread_id, proposal_id, status, reason))

    @app.get(
        "/api/plan",
        tags=["plan"],
        response_model=PlanOut,
        responses={**_ERRORS, 204: {"description": "No plan has been approved yet."}},
    )
    def active_plan(thread_id: ThreadId) -> Response | PlanOut:
        plan = conversation.active_plan(thread_id)
        if plan is None:
            return Response(status_code=204)
        return present.plan(plan)

    @app.post("/api/events", tags=["events"], response_model=TurnOut, responses=_ERRORS)
    def report_event(body: EventIn, thread_id: ThreadId) -> TurnOut | JSONResponse:
        try:
            event = Event(
                event_id=new_event_id(),
                source=EventSource.USER,
                timestamp=clock(),
                **body.model_dump(),
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            return JSONResponse(status_code=422, content={"detail": first["msg"]})
        return present.turn(conversation.report_events(thread_id, [event]))

    @app.post(
        "/api/events/confirm",
        tags=["events"],
        response_model=TurnOut,
        responses=_ERRORS,
    )
    def confirm_events(body: EventsConfirmIn, thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.confirm_events(thread_id, body.confirmed))

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app


__all__ = ["MultipleWorkersError", "create_app"]
