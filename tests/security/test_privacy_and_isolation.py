"""Accessibility data stays out of state, prompts and logs without consent; threads stay apart."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from security.sec_harness import (
    APPROVAL,
    BENIGN_EXTRACTION,
    BENIGN_MESSAGE,
    CONFIRMATION,
    RecordingNarrator,
    Rig,
    interrupt_of,
    kind_of,
    make_rig,
)

ACCESSIBILITY_EXTRACTION: dict[str, Any] = {
    **BENIGN_EXTRACTION,
    "accessibility": [
        {
            "guest_ref": 2,
            "daily_walking_limit_minutes": 1357,
            "rest_frequency_minutes": 2468,
            "heat_sensitivity": True,
            "mobility_requirements": ["LIMITED_WALKING"],
            "ride_restrictions": ["NOT_RECOMMENDED_HEART_CONDITION"],
        }
    ],
}
ACCESSIBILITY_TOKENS = (
    "LIMITED_WALKING",
    "HEART_CONDITION",
    "heat_sensitivity=True",
    "daily_walking_limit_minutes=1357",
    "rest_frequency_minutes=2468",
    "1357 min",
    "2468 min",
)


def _state_text(rig: Rig, thread_id: str | None = None) -> str:
    values = rig.values(thread_id)
    return repr({k: v for k, v in values.items() if k != "messages"})


def _interrupt_text(results: Sequence[Mapping[str, Any]]) -> str:
    return repr([interrupt_of(r) for r in results])


def _leaks(text: str) -> list[str]:
    return [t for t in ACCESSIBILITY_TOKENS if t in text]


def _full_journey(rig: Rig) -> None:
    rig.start(BENIGN_MESSAGE)
    rig.confirm()
    rig.approve()


def test_accessibility_details_are_not_in_state_before_confirmation() -> None:
    rig = make_rig(ACCESSIBILITY_EXTRACTION)

    rig.start(BENIGN_MESSAGE)

    assert _leaks(_state_text(rig)) == []
    assert (
        rig.values()["pending_hard_constraint_confirmation"][-1] == "accessibility:g2"
    )


def test_confirmation_interrupt_carries_guest_ids_but_no_accessibility_details() -> (
    None
):
    rig = make_rig(ACCESSIBILITY_EXTRACTION)

    result = rig.start(BENIGN_MESSAGE)

    payload = interrupt_of(result)
    assert payload is not None and payload["kind"] == CONFIRMATION
    assert payload["accessibility_guests"] == ["g2"]
    assert _leaks(repr(payload)) == []


def test_nothing_is_stored_until_the_guests_consent() -> None:
    rig = make_rig(ACCESSIBILITY_EXTRACTION)
    rig.start(BENIGN_MESSAGE)

    without_consent = rig.resume({"confirmed": True})
    explicit_no = rig.resume({"confirmed": True, "consent": False})

    for result in (without_consent, explicit_no):
        payload = interrupt_of(result)
        assert payload is not None and payload["kind"] == CONFIRMATION
        assert payload["reason"] == "consent_required"
    assert rig.store.puts == 0
    assert rig.store.records == {}
    assert rig.values().get("accessibility_ref") in (None, [])
    assert rig.active_plan() is None


def test_declining_the_confirmation_discards_the_staged_accessibility_flags() -> None:
    rig = make_rig(ACCESSIBILITY_EXTRACTION)
    rig.start(BENIGN_MESSAGE)

    rig.resume({"confirmed": False, "correction": "Actually we are three adults."})

    assert rig.store.puts == 0
    assert rig.store.records == {}
    assert _leaks(_state_text(rig)) == []


def test_consent_stores_the_flags_only_in_the_session_store_and_the_ref_in_state() -> (
    None
):
    rig = make_rig(ACCESSIBILITY_EXTRACTION)
    rig.start(BENIGN_MESSAGE)

    result = rig.confirm()

    assert kind_of(result) == APPROVAL
    assert set(rig.store.records) == {("t-sec", "g2")}
    assert rig.store.records[("t-sec", "g2")].consent is True
    assert rig.store.records[("t-sec", "g2")].retention_policy == "session_only"
    assert rig.values()["accessibility_ref"] == ["g2"]
    assert _leaks(_state_text(rig)) == []


def test_narrator_payload_and_explanation_carry_no_accessibility_details_even_with_consent() -> (
    None
):
    narrator = RecordingNarrator()
    rig = make_rig(ACCESSIBILITY_EXTRACTION, narrator=narrator)
    rig.start(BENIGN_MESSAGE)
    rig.confirm()

    explanation = str(rig.values().get("explanation"))
    proposals = " ".join(p.explanation for p in rig.proposals())

    assert narrator.payloads
    assert _leaks(" ".join(narrator.payloads)) == []
    assert _leaks(" ".join(h or "" for h in narrator.hints)) == []
    assert _leaks(explanation) == []
    assert _leaks(proposals) == []


def test_every_interrupt_of_the_journey_is_free_of_accessibility_details() -> None:
    rig = make_rig(ACCESSIBILITY_EXTRACTION)

    _full_journey(rig)

    assert _leaks(_interrupt_text(rig.seen)) == []


def test_accessibility_details_never_reach_the_logs() -> None:
    rig = make_rig(ACCESSIBILITY_EXTRACTION, narrator=RecordingNarrator())
    logger = logging.getLogger()
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.DEBUG)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        _full_journey(rig)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    logged = "\n".join(r.getMessage() for r in records)
    assert records
    assert _leaks(logged) == []


def test_guest_label_and_height_are_not_sent_to_the_narrator() -> None:
    extraction = {
        "party_size": 2,
        "guests": [
            {"role": "adult", "label": "Zelda"},
            {"role": "child", "height_cm": 130},
        ],
        "departure_time": "20:00",
    }
    narrator = RecordingNarrator()
    rig = make_rig(extraction, narrator=narrator)
    rig.start("Me and my son, he is 130 cm tall. We leave the park at 8 PM.")
    result = rig.confirm()

    payload = " ".join(narrator.payloads).lower()

    assert kind_of(result) == APPROVAL
    assert narrator.payloads
    assert "zelda" not in payload
    assert "height" not in payload


class MessageKeyedExtractor:
    """Reads a party label from the guests' own words, so each thread's output differs."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        self.calls.append((list(human_messages), repair_hint))
        joined = " ".join(human_messages)
        name = "Alpha" if "Alpha" in joined else "Bravo"
        extraction: dict[str, Any] = {
            "party_size": 2,
            "guests": [{"role": "adult", "label": name}, {"role": "adult"}],
            "departure_time": "20:00",
        }
        if name == "Alpha":
            extraction["accessibility"] = ACCESSIBILITY_EXTRACTION["accessibility"]
        return extraction


def test_two_interleaved_threads_never_see_each_others_data() -> None:
    narrator = RecordingNarrator()
    rig = make_rig(extractor=MessageKeyedExtractor(), narrator=narrator)
    base = "Two adults, we leave the park at 8 PM."
    a, b = "thread-a", "thread-b"

    first_a = rig.start(f"Family Alpha here. {base}", thread_id=a)
    first_b = rig.start(f"Family Bravo here. {base}", thread_id=b)
    second_a = rig.confirm(a)
    second_b = rig.confirm(b)
    third_b = rig.approve(b)
    third_a = rig.approve(a)

    a_text = repr([interrupt_of(r) for r in (first_a, second_a, third_a)])
    b_text = repr([interrupt_of(r) for r in (first_b, second_b, third_b)])
    assert "Bravo" not in a_text and "Alpha" not in b_text
    assert "Bravo" not in repr(rig.values(a)) and "Alpha" not in repr(rig.values(b))
    assert set(rig.store.records) == {(a, "g2")}
    assert rig.values(b).get("accessibility_ref") in (None, [])
    assert rig.active_plan(a) is not None and rig.active_plan(b) is not None
    assert rig.active_plan(a).plan_id != rig.active_plan(b).plan_id  # type: ignore[union-attr]
    assert all(
        "Alpha" not in payload and "Bravo" not in payload
        for payload in narrator.payloads
    )


@pytest.mark.parametrize("approver", ["a", "b"])
def test_approving_one_thread_does_not_activate_the_other(approver: str) -> None:
    rig = make_rig(BENIGN_EXTRACTION)
    ids = {"a": "thread-a", "b": "thread-b"}
    for tid in ids.values():
        rig.start(BENIGN_MESSAGE, thread_id=tid)
        rig.confirm(tid)

    rig.approve(ids[approver])

    other = ids["b" if approver == "a" else "a"]
    assert rig.active_plan(ids[approver]) is not None
    assert rig.active_plan(other) is None
    assert rig.values(other).get("current_plan") is None
