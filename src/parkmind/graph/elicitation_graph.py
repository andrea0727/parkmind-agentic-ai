"""Elicitation and hard-constraint confirmation.

Workflow::

    START -> elicit -+-> ask_missing (interrupt) -> elicit            (info missing)
                     +-> confirm_hard_constraints (interrupt)
                            +-> elicit                                (human corrected)
                            +-> validate_constraints -> downstream -> END

The invariant: a newly extracted hard constraint cannot reach the checker
without a human confirming it. ``elicit`` only *proposes* (``constraints_valid``
stays False and ``pending_hard_constraint_confirmation`` lists what is
unconfirmed); only the resumed, human-driven ``confirm_hard_constraints`` clears
the list, and ``validate_constraints`` is the only writer of
``constraints_valid=True``. ``downstream`` -- where P0-30 plugs in load_context
and the checker -- starts with ``ensure_confirmed``, so it fails loudly if that
ever stops being true.

Privacy [C19]: interrupt payloads and the pending list are checkpointed, so for
accessibility they carry guest ids only. The flags wait in the intake use case
and move to the SessionStore on confirmation. Consent and retention come from
the human's resume value, never from the LLM; retention defaults to
``session_only``.

Resume values
-------------
``ask_missing``: the answer text (``str``).
``confirm_hard_constraints``: ``{"confirmed": bool, "consent": bool,
"retention_policy": "session_only" | "persisted", "correction": str}``.

Nothing before an ``interrupt()`` has a side effect: LangGraph re-runs a node
from the top on resume.
"""

from collections.abc import Mapping
from typing import Any, NamedTuple, Protocol

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from parkmind.agents.elicit_agent import (
    UNAVAILABLE_FLAG,
    AnthropicExtractor,
    GuestInfoExtractor,
    make_elicit_node,
)
from parkmind.core.language import DEFAULT_LANGUAGE, text
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import solo_of
from parkmind.services.use_cases.accessibility_intake import (
    DEFAULT_RETENTION,
    AccessibilityIntakeUseCase,
    RetentionPolicy,
)
from parkmind.services.use_cases.resolve_attraction_names import AttractionNameResolver

_RETENTION_POLICIES = ("session_only", "persisted")


class StateNode(Protocol):
    def __call__(self, state: ParkMindState) -> Any: ...


class _Decision(NamedTuple):
    confirmed: bool
    consent: bool
    retention: RetentionPolicy
    correction: str


def _parse_confirmation(value: Any) -> _Decision | str:
    """The human's decision, or why the resume value is not valid."""
    if not isinstance(value, Mapping):
        return "the decision must be a mapping with a 'confirmed' key"
    confirmed = value.get("confirmed")
    if not isinstance(confirmed, bool):
        return "'confirmed' must be true or false"
    consent = value.get("consent")
    if consent is not None and not isinstance(consent, bool):
        return "'consent' must be true or false"
    correction = value.get("correction")
    if correction is not None and not isinstance(correction, str):
        return "'correction' must be text"
    retention: RetentionPolicy = DEFAULT_RETENTION
    raw_retention = value.get("retention_policy")
    if raw_retention in _RETENTION_POLICIES:
        retention = raw_retention
    elif confirmed and raw_retention:
        return "'retention_policy' must be 'session_only' or 'persisted'"
    return _Decision(confirmed, bool(consent), retention, (correction or "").strip())


class UnconfirmedHardConstraintsError(RuntimeError):
    """Hard constraints reached a step that requires them to be confirmed."""


def ensure_confirmed(state: ParkMindState) -> None:
    """Guard for every step that feeds the checker."""
    if state.get("constraints") is None:
        raise UnconfirmedHardConstraintsError("no constraints have been extracted")
    if state.get("pending_hard_constraint_confirmation"):
        raise UnconfirmedHardConstraintsError(
            "hard constraints are awaiting confirmation and cannot be used yet"
        )
    if not state.get("constraints_valid"):
        raise UnconfirmedHardConstraintsError("constraints have not been validated")


def _last_message_type(state: ParkMindState) -> str | None:
    messages = state.get("messages") or []
    return getattr(messages[-1], "type", None) if messages else None


def _route_after_extraction(state: ParkMindState) -> str:
    if state.get("constraints") is not None:
        return "confirm_hard_constraints"
    messages = state.get("messages") or []
    if messages and getattr(messages[-1], "additional_kwargs", {}).get(
        UNAVAILABLE_FLAG
    ):
        return END
    # An AI message is a question waiting for an answer; a human message is new input.
    return "ask_missing" if _last_message_type(state) == "ai" else "elicit"


def _ask_missing(state: ParkMindState) -> dict[str, Any]:
    last = state["messages"][-1]
    answer = interrupt(
        {
            "kind": "missing_information",
            "missing": list(last.additional_kwargs.get("missing_information", [])),
            "question": last.content,
        }
    )
    return {"messages": [HumanMessage(content=str(answer))]}


def _make_confirm_node(
    intake: AccessibilityIntakeUseCase,
) -> StateNode:
    def confirm_hard_constraints(state: ParkMindState) -> dict[str, Any]:
        session_id = state["thread_id"]
        lang = state.get("language") or DEFAULT_LANGUAGE
        solo = solo_of(state)
        pending = list(state.get("pending_hard_constraint_confirmation") or [])
        if not pending:
            return {}

        constraints = state.get("constraints")
        labels = (
            {g.guest_id: g.label for g in constraints.guests} if constraints else {}
        )
        confirmation = intake.confirmation(pending, lang, solo=solo, labels=labels)
        accessibility_ids = list(confirmation.accessibility_guests)
        payload: dict[str, Any] = {
            "kind": "hard_constraint_confirmation",
            "pending": pending,
            "echo": list(confirmation.echo),
            "accessibility_guests": accessibility_ids,
        }

        # A malformed value is asked again with an "error" instead of failing the run.
        # Accessibility data can only be used with consent; keep asking until the
        # human consents or says the constraint is wrong.
        while True:
            parsed = _parse_confirmation(interrupt(payload))
            if isinstance(parsed, str):
                payload = {**payload, "error": parsed}
                continue
            if accessibility_ids and parsed.confirmed and not parsed.consent:
                payload = {
                    **{k: v for k, v in payload.items() if k != "error"},
                    "reason": "consent_required",
                }
                continue
            break
        decision = parsed

        if not decision.confirmed:
            intake.discard(session_id)
            if decision.correction:
                new_message: Any = HumanMessage(content=decision.correction)
            else:
                new_message = AIMessage(
                    content=text("what_to_change", lang),
                    additional_kwargs={"missing_information": ["correction"]},
                )
            return {
                "constraints": None,
                "constraints_valid": False,
                "pending_hard_constraint_confirmation": None,
                "messages": [new_message],
            }

        committed = intake.commit(
            session_id, consent=True, retention_policy=decision.retention
        )
        if committed != accessibility_ids:
            # The staged flags are gone (e.g. process restart). Never proceed
            # without them: re-extract from the conversation and ask again.
            intake.discard(session_id)
            return {
                "constraints": None,
                "constraints_valid": False,
                "pending_hard_constraint_confirmation": None,
            }
        return {
            "pending_hard_constraint_confirmation": [],
            "accessibility_ref": sorted(
                set(state.get("accessibility_ref") or []) | set(committed)
            ),
        }

    return confirm_hard_constraints


def _validate_constraints(state: ParkMindState) -> dict[str, Any]:
    constraints = state.get("constraints")
    if constraints is None:
        raise UnconfirmedHardConstraintsError("no constraints to validate")
    if state.get("pending_hard_constraint_confirmation"):
        raise UnconfirmedHardConstraintsError("hard constraints are still unconfirmed")
    guest_ids = {g.guest_id for g in constraints.guests}
    unknown = set(state.get("accessibility_ref") or []) - guest_ids
    if unknown:
        raise ValueError(
            f"accessibility_ref names guests outside the party: {sorted(unknown)}"
        )
    if constraints.party_size != len(constraints.guests):
        raise ValueError("party_size does not match the number of guests")
    return {"constraints_valid": True}


def _after_confirmation(state: ParkMindState) -> str:
    if state.get("constraints") is not None:
        return "validate_constraints"
    return _route_after_extraction(state)


def _proceed(state: ParkMindState) -> dict[str, Any]:
    """Placeholder for load_context -> checker [P0-30]; refuses unconfirmed input."""
    ensure_confirmed(state)
    return {}


def build_elicitation_graph(
    extractor: GuestInfoExtractor | None = None,
    intake: AccessibilityIntakeUseCase | None = None,
    checkpointer: Any = None,
    downstream: StateNode = _proceed,
    *,
    names: AttractionNameResolver,
) -> Any:
    """Compile the elicit -> confirm -> validate graph.

    ``names`` turns the must-do/avoid names the guests say into catalog
    ``node_id``s; the confirmed ``constraints.must_do``/``avoid`` hold ids.

    ``downstream`` runs only once constraints are confirmed and valid; give it
    the next stage of the initial planning graph [P0-30]. It should call
    ``ensure_confirmed`` first.
    """
    intake = intake or AccessibilityIntakeUseCase()
    graph = StateGraph(ParkMindState)

    graph.add_node(
        "elicit", make_elicit_node(extractor or AnthropicExtractor(), intake, names)
    )
    graph.add_node("ask_missing", _ask_missing)
    graph.add_node("confirm_hard_constraints", _make_confirm_node(intake))
    graph.add_node("validate_constraints", _validate_constraints)
    graph.add_node("downstream", downstream)

    graph.add_edge(START, "elicit")
    graph.add_conditional_edges(
        "elicit",
        _route_after_extraction,
        ["ask_missing", "confirm_hard_constraints", "elicit", END],
    )
    graph.add_edge("ask_missing", "elicit")
    graph.add_conditional_edges(
        "confirm_hard_constraints",
        _after_confirmation,
        ["validate_constraints", "ask_missing", "elicit"],
    )
    graph.add_edge("validate_constraints", "downstream")
    graph.add_edge("downstream", END)

    return graph.compile(checkpointer=checkpointer or default_checkpointer())
