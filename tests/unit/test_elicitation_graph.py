"""elicit -> confirm -> validate graph: a new hard constraint needs confirmation."""

from typing import Any

import pytest
from elicit_support import FakeExtractor, make_intake, scenario
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import (
    UnconfirmedHardConstraintsError,
    build_elicitation_graph,
    ensure_confirmed,
)

COMPLETE = scenario("wiki_example_complete")
MISSING_DEPARTURE = scenario("wiki_example_missing_departure")
PURE = scenario("pure_preferences_no_accessibility")
BAD_OUTPUT = {"party_size": 9, "guests": [{"role": "adult"}]}
CONFIG: Any = {"configurable": {"thread_id": "t1"}}
ACCESSIBILITY_MARKERS = (
    b"mobility_requirements",
    b"daily_walking_limit_minutes",
    b"rest_frequency_minutes",
    b"retention_policy",
    b"LIMITED_WALKING",
)


class Downstream:
    """Stands in for load_context -> checker; records the states that reach it."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def __call__(self, state: Any) -> dict[str, Any]:
        ensure_confirmed(state)
        self.seen.append(dict(state))
        return {}


def _graph(extractor: FakeExtractor, saver: MemorySaver | None = None):
    intake, store = make_intake()
    downstream = Downstream()
    saver = saver or default_checkpointer()
    graph = build_elicitation_graph(extractor, intake, saver, downstream)
    return graph, intake, store, downstream, saver


def _start(graph: Any, *texts: str) -> dict[str, Any]:
    return graph.invoke(
        {"thread_id": "t1", "messages": [HumanMessage(content=t) for t in texts]}, config=CONFIG
    )


def _resume(graph: Any, value: Any) -> dict[str, Any]:
    return graph.invoke(Command(resume=value), config=CONFIG)


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any]:
    return result["__interrupt__"][0].value


CONFIRM = {"confirmed": True, "consent": True}


# --- a hard constraint cannot reach the checker without confirmation ------------


def test_hard_constraint_waits_for_confirmation_and_never_reaches_downstream() -> None:
    graph, _, _, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))

    result = _start(graph, *COMPLETE["messages"])

    payload = _interrupt_value(result)
    assert payload["kind"] == "hard_constraint_confirmation"
    assert payload["pending"] == COMPLETE["expect_pending"]
    assert downstream.seen == []
    state = graph.get_state(CONFIG).values
    assert state["constraints_valid"] is False
    assert state["pending_hard_constraint_confirmation"]
    with pytest.raises(UnconfirmedHardConstraintsError):
        ensure_confirmed(state)


def test_confirmation_releases_validated_constraints_downstream() -> None:
    graph, _, _, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])

    result = _resume(graph, CONFIRM)

    assert "__interrupt__" not in result
    assert len(downstream.seen) == 1
    reached = downstream.seen[0]
    assert reached["constraints_valid"] is True
    assert reached["pending_hard_constraint_confirmation"] == []
    assert reached["accessibility_ref"] == ["g2"]
    assert reached["constraints"].must_do == ["TRON", "Space Mountain"]


def test_ensure_confirmed_rejects_every_unconfirmed_shape() -> None:
    graph, *_ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])
    proposed = graph.get_state(CONFIG).values

    with pytest.raises(UnconfirmedHardConstraintsError):
        ensure_confirmed({})
    with pytest.raises(UnconfirmedHardConstraintsError):
        ensure_confirmed({**proposed, "pending_hard_constraint_confirmation": []})  # not validated
    with pytest.raises(UnconfirmedHardConstraintsError):
        ensure_confirmed({**proposed, "constraints_valid": True})  # still pending
    ensure_confirmed({**proposed, "constraints_valid": True, "pending_hard_constraint_confirmation": []})


def test_rejecting_the_echo_drops_the_proposal_and_reextracts_with_the_correction() -> None:
    corrected = scenario("wheelchair_and_avoid")
    extractor = FakeExtractor(COMPLETE["extraction"], corrected["extraction"])
    graph, intake, store, downstream, _ = _graph(extractor)
    _start(graph, *COMPLETE["messages"])

    result = _resume(
        graph, {"confirmed": False, "correction": "No wait, my husband uses a wheelchair."}
    )

    assert _interrupt_value(result)["kind"] == "hard_constraint_confirmation"
    assert extractor.calls[1][0][-1] == "No wait, my husband uses a wheelchair."
    assert downstream.seen == [] and store.puts == 0
    assert intake.pending_guest_ids("t1") == ["g2"]  # the corrected statement, re-staged


def test_rejecting_without_a_correction_asks_what_to_change() -> None:
    graph, intake, store, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])

    result = _resume(graph, {"confirmed": False})

    payload = _interrupt_value(result)
    assert payload["kind"] == "missing_information"
    assert payload["missing"] == ["correction"]
    assert intake.pending_guest_ids("t1") == []
    assert downstream.seen == [] and store.puts == 0


def test_only_hard_items_without_accessibility_still_need_confirmation() -> None:
    graph, _, store, downstream, _ = _graph(FakeExtractor(PURE["extraction"]))

    result = _start(graph, *PURE["messages"])

    assert _interrupt_value(result)["pending"] == ["departure_time: 21:00"]
    assert _interrupt_value(result)["accessibility_guests"] == []
    assert downstream.seen == []
    _resume(graph, {"confirmed": True})  # no accessibility -> no consent needed
    assert len(downstream.seen) == 1 and store.puts == 0


# --- missing information is surfaced ---------------------------------------------


def test_missing_information_interrupts_and_resumes_with_the_answer() -> None:
    extractor = FakeExtractor(MISSING_DEPARTURE["extraction"], COMPLETE["extraction"])
    graph, _, _, downstream, _ = _graph(extractor)

    result = _start(graph, *MISSING_DEPARTURE["messages"])

    payload = _interrupt_value(result)
    assert payload["kind"] == "missing_information"
    assert payload["missing"] == ["departure_time"]
    assert downstream.seen == []

    result = _resume(graph, "We leave the park at 8 PM.")

    assert _interrupt_value(result)["kind"] == "hard_constraint_confirmation"
    assert extractor.calls[1][0][-1] == "We leave the park at 8 PM."
    assert len(extractor.calls[1][0]) == 2


# --- schema failure is recoverable -----------------------------------------------


def test_schema_failure_is_retried_inside_the_node() -> None:
    extractor = FakeExtractor(BAD_OUTPUT, COMPLETE["extraction"])
    graph, *_ = _graph(extractor)

    result = _start(graph, *COMPLETE["messages"])

    assert _interrupt_value(result)["kind"] == "hard_constraint_confirmation"
    assert len(extractor.calls) == 2


def test_persistent_schema_failure_asks_to_rephrase_then_recovers() -> None:
    extractor = FakeExtractor(BAD_OUTPUT, BAD_OUTPUT, COMPLETE["extraction"])
    graph, *_ = _graph(extractor)

    result = _start(graph, *COMPLETE["messages"])

    payload = _interrupt_value(result)
    assert payload["kind"] == "missing_information"
    assert payload["missing"] == ["unreadable_response"]

    result = _resume(graph, "Let me say that again: four of us, leaving at 8 PM.")

    assert _interrupt_value(result)["kind"] == "hard_constraint_confirmation"


# --- consent and retention come from the human ------------------------------------


def test_accessibility_cannot_be_committed_without_consent() -> None:
    graph, intake, store, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])

    result = _resume(graph, {"confirmed": True, "consent": False})

    payload = _interrupt_value(result)
    assert payload["kind"] == "hard_constraint_confirmation"
    assert payload["reason"] == "consent_required"
    assert store.puts == 0 and downstream.seen == []
    assert intake.pending_guest_ids("t1") == ["g2"]

    _resume(graph, {"confirmed": True, "consent": True})

    assert len(downstream.seen) == 1
    record = store.get("t1", "g2")
    assert record is not None
    assert record.consent is True and record.retention_policy == "session_only"


def test_retention_policy_is_taken_from_the_human_decision() -> None:
    graph, _, store, _, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])

    _resume(graph, {"confirmed": True, "consent": True, "retention_policy": "persisted"})

    record = store.get("t1", "g2")
    assert record is not None and record.retention_policy == "persisted"


def test_unknown_retention_policy_is_rejected() -> None:
    graph, _, store, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])

    with pytest.raises(ValueError, match="retention_policy"):
        _resume(graph, {"confirmed": True, "consent": True, "retention_policy": "forever"})

    assert store.puts == 0 and downstream.seen == []


def test_lost_staging_never_lets_unconfirmed_accessibility_through() -> None:
    """If the staged flags vanish (e.g. a restart) the checker must not run without them."""
    graph, intake, store, downstream, _ = _graph(FakeExtractor(COMPLETE["extraction"]))
    _start(graph, *COMPLETE["messages"])
    intake.discard("t1")

    result = _resume(graph, CONFIRM)

    assert downstream.seen == [] and store.puts == 0
    assert _interrupt_value(result)["kind"] == "hard_constraint_confirmation"
    assert intake.pending_guest_ids("t1") == ["g2"]  # re-extracted and asked again


# --- accessibility is not stored as a soft preference or in the checkpoint --------


def test_accessibility_is_neither_a_soft_preference_nor_in_checkpoints() -> None:
    graph, intake, _, _, saver = _graph(FakeExtractor(COMPLETE["extraction"]))

    result = _start(graph, *COMPLETE["messages"])

    payload = _interrupt_value(result)
    assert "accessibility:g2" in payload["pending"]
    assert payload["accessibility_guests"] == ["g2"]
    assert "walk" not in str(payload).lower()
    assert intake.describe("t1") == {"g2": "limited walking"}

    profiles = graph.get_state(CONFIG).values["guest_profiles"]
    assert "LIMITED_WALKING" not in " ".join(p.model_dump_json() for p in profiles)

    _resume(graph, CONFIRM)

    blob = _all_serialized_bytes(saver)
    assert blob
    for marker in ACCESSIBILITY_MARKERS:
        assert marker not in blob, marker


def _all_serialized_bytes(saver: MemorySaver) -> bytes:
    chunks: list[bytes] = []
    for checkpoints in saver.storage.values():
        for versions in checkpoints.values():
            for checkpoint_tuple, _metadata, _parent in versions.values():
                chunks.append(checkpoint_tuple[1])
    for writes in saver.writes.values():
        for _task_id, _channel, payload, _task_path in writes.values():
            chunks.append(payload[1])
    return b"".join(chunks)

