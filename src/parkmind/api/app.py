"""FastAPI application (P0-35): thin routes over a ``Conversation``.

A route validates its body, makes one call on the conversation and presents the result. It
never plans, checks or decides anything; the planner's gates are behind the conversation.
"""

import os
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
    LoginIn,
    MessageIn,
    ParkStatusOut,
    PlanOut,
    ProblemOut,
    ProposalOut,
    SessionOut,
    TokenOut,
    TurnOut,
)
from parkmind.api.security import (
    CONTENT_SECURITY_POLICY,
    SECURITY_HEADERS,
    AuthConfig,
    Limits,
    RateLimiter,
)
from parkmind.core.contracts import PARK_TZ, ApprovalStatus, Event, EventSource
from parkmind.core.conversation import (
    Conversation,
    ConversationError,
    HardConstraintAnswer,
    NotExpectedError,
    PlanEditRequest,
    StaleProposalError,
    UnknownAttractionError,
    UnknownThreadError,
)
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase
from parkmind.services.use_cases.park_status import ParkStatusUseCase
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
    401: {"model": ProblemOut, "description": "Missing, invalid or expired token."},
    429: {"model": ProblemOut, "description": "Too many requests."},
    404: {"model": ProblemOut, "description": "Unknown session."},
    409: {"model": ProblemOut, "description": "The planner is not waiting for that."},
    503: {"model": ProblemOut, "description": "Park data or routing unavailable."},
}

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
_ANONYMOUS = "anonymous"

# Per username and per client address: wrong passwords in a row lock the login for a while.
_LOGIN_FAILURES = 5
_LOGIN_FAILURES_PER_ADDRESS = 20
_LOGIN_WINDOW_SECONDS = 300


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
    park_status: ParkStatusUseCase | None = None,
    clock: Callable[[], datetime] = _park_now,
    new_event_id: Callable[[], str] = _new_event_id,
    ui_dir: Path | None = None,
    cors_origins: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    auth: AuthConfig | None = None,
    limits: Limits | None = None,
    saved_parties: Mapping[str, str] | None = None,
) -> FastAPI:
    """``auth=None`` leaves every route open: only for tests and an explicit demo opt-out.

    ``saved_parties`` maps a username to the party saved for that account, as planner-ready text.
    """

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
            {"name": "auth", "description": "Sign in for an access token."},
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
            allow_headers=["Content-Type", "Authorization"],
        )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response: Response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith(_DOC_PATHS):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    present = Presenter(describe or DescribePlanUseCase())

    bearer = HTTPBearer(auto_error=False)
    owners: dict[str, str] = {}
    owners_lock = threading.Lock()
    login_failures = RateLimiter(_LOGIN_FAILURES, _LOGIN_WINDOW_SECONDS)
    login_by_address = RateLimiter(_LOGIN_FAILURES_PER_ADDRESS, _LOGIN_WINDOW_SECONDS)
    admission = threading.Lock()
    limits = limits or Limits()
    minute_limit = RateLimiter(limits.messages_per_minute, 60)
    user_day_limit = RateLimiter(limits.messages_per_user_day, 86400)
    global_day_limit = RateLimiter(limits.messages_global_day, 86400)
    session_limit = RateLimiter(limits.sessions_per_hour, 3600)

    def _unauthorized() -> HTTPException:
        return HTTPException(
            status_code=401,
            detail="Sign in to use the API.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if auth is None:
            return _ANONYMOUS
        user = auth.verify_token(credentials.credentials) if credentials else None
        if user is None:
            raise _unauthorized()
        return user

    User = Annotated[str, Depends(current_user)]

    def owned_thread(
        user: User,
        thread_id: Annotated[
            str,
            Query(min_length=1, max_length=64, description="From POST /api/session."),
        ],
    ) -> str:
        """A session belongs to whoever started it; anyone else sees it as unknown."""
        with owners_lock:
            owner = owners.get(thread_id)
        if auth is not None and owner != user:
            raise UnknownThreadError(f"unknown session {thread_id!r}")
        return thread_id

    ThreadId = Annotated[str, Depends(owned_thread)]

    def _admit(*gates: tuple[RateLimiter, str]) -> None:
        """All gates have room, or 429 with the wait of the one that is full; hits count only on success."""
        with admission:
            for limiter, key in gates:
                if not limiter.check(key):
                    raise HTTPException(
                        status_code=429,
                        detail="Too many requests; slow down and try again shortly.",
                        headers={"Retry-After": str(limiter.retry_after(key))},
                    )
            for limiter, key in gates:
                limiter.hit(key)

    def llm_budget(user: User) -> None:
        """Routes that advance the graph: per-minute and per-day per user, plus a global day cap."""
        _admit((minute_limit, user), (user_day_limit, user), (global_day_limit, "global"))

    Metered = Depends(llm_budget)

    @app.exception_handler(UnknownThreadError)
    async def _unknown(_: Request, exc: UnknownThreadError) -> JSONResponse:
        return _problem(404, exc)

    @app.exception_handler(NotExpectedError)
    async def _not_expected(_: Request, exc: NotExpectedError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(StaleProposalError)
    async def _stale(_: Request, exc: StaleProposalError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(UnknownAttractionError)
    async def _unknown_attraction(_: Request, exc: UnknownAttractionError) -> JSONResponse:
        return _problem(422, exc)

    @app.exception_handler(ConversationError)
    async def _conversation(_: Request, exc: ConversationError) -> JSONResponse:
        return _problem(409, exc)

    @app.exception_handler(PlanningUnavailableError)
    async def _unavailable(_: Request, exc: PlanningUnavailableError) -> JSONResponse:
        return _problem(503, exc)

    @app.get("/api/health", tags=["conversation"], response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut(auth_required=auth is not None)

    if auth is not None:

        @app.post(
            "/api/login",
            tags=["auth"],
            response_model=TokenOut,
            responses={401: _ERRORS[401], 429: _ERRORS[429]},
        )
        def login(body: LoginIn, request: Request) -> TokenOut:
            origin = request.client.host if request.client else "unknown"
            pair = f"{origin}|{body.username}"
            gates = ((login_failures, pair), (login_by_address, origin))
            with admission:  # reserve the attempt first so parallel guesses cannot all slip in
                for limiter, key in gates:
                    if not limiter.check(key):
                        raise HTTPException(
                            status_code=429,
                            detail="Too many failed sign-ins; try again in a few minutes.",
                            headers={"Retry-After": str(limiter.retry_after(key))},
                        )
                for limiter, key in gates:
                    limiter.hit(key)
            if not auth.authenticate(body.username, body.password):
                raise HTTPException(
                    status_code=401,
                    detail="Wrong username or password.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            login_failures.reset(pair)
            login_by_address.release(origin)
            return TokenOut(
                access_token=auth.issue_token(body.username),
                expires_in=auth.token_ttl_seconds,
            )

    @app.post(
        "/api/session",
        tags=["conversation"],
        response_model=SessionOut,
        status_code=201,
        responses={401: _ERRORS[401], 429: _ERRORS[429]},
    )
    def start_session(user: User) -> SessionOut:
        _admit((session_limit, user))
        thread_id = conversation.start((saved_parties or {}).get(user))
        with owners_lock:
            owners[thread_id] = user
        status = park_status.execute(clock()) if park_status is not None else None
        return SessionOut(
            thread_id=thread_id,
            park=ParkStatusOut(
                closed=status.closed,
                opening_time=status.opening_time,
                closing_time=status.closing_time,
            )
            if status is not None
            else None,
        )

    @app.post(
        "/api/message",
        tags=["conversation"],
        response_model=TurnOut,
        responses=_ERRORS,
        dependencies=[Metered],
    )
    def say(body: MessageIn, thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.say(thread_id, body.text, body.lang))

    @app.post(
        "/api/constraints/confirm",
        tags=["conversation"],
        response_model=TurnOut,
        responses=_ERRORS,
        dependencies=[Metered],
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
        dependencies=[Metered],
    )
    def decide(
        proposal_id: str,
        decision: Literal["approve", "reject", "edit"],
        thread_id: ThreadId,
        body: DecisionIn | None = None,
    ) -> TurnOut | JSONResponse:
        body = body or DecisionIn()
        if decision == "edit":
            if not body.remove and not body.add:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "an edit needs at least one id in 'remove' or 'add'"},
                )
            turn = conversation.decide(
                thread_id,
                proposal_id,
                ApprovalStatus.EDITED,
                edit=PlanEditRequest(remove=tuple(body.remove), add=tuple(body.add)),
            )
            if turn.error:
                return JSONResponse(status_code=422, content={"detail": turn.error})
            return present.turn(turn)
        if decision == "approve":
            return present.turn(
                conversation.decide(thread_id, proposal_id, ApprovalStatus.APPROVED)
            )
        return present.turn(
            conversation.decide(thread_id, proposal_id, ApprovalStatus.REJECTED, body.reason)
        )

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

    @app.post(
        "/api/events",
        tags=["events"],
        response_model=TurnOut,
        responses=_ERRORS,
        dependencies=[Metered],
    )
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
        dependencies=[Metered],
    )
    def confirm_events(body: EventsConfirmIn, thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.confirm_events(thread_id, body.confirmed))

    @app.post(
        "/api/provisional",
        tags=["plan"],
        response_model=TurnOut,
        responses=_ERRORS,
    )
    def provisional_plan(thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.provisional(thread_id))

    @app.post(
        "/api/provisional/refresh",
        tags=["plan"],
        response_model=TurnOut,
        responses=_ERRORS,
        dependencies=[Metered],
    )
    def refresh_provisional_plan(thread_id: ThreadId) -> TurnOut:
        return present.turn(conversation.refresh_provisional(thread_id))

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app


__all__ = ["MultipleWorkersError", "create_app"]
