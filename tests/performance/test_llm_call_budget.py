"""Every plan costs a bounded number of model calls and a bounded payload."""

import pytest

from parkmind.config.settings import settings
from performance.perf_budgets import (
    EXTRACTOR_CALLS_HAPPY_PATH,
    EXTRACTOR_CALLS_REPAIR_PATH,
    NARRATOR_CALLS_HAPPY_PATH,
    NARRATOR_CALLS_REPAIR_PATH,
    NARRATOR_PAYLOAD_TOKENS_MUST_DO_12,
    NARRATOR_PAYLOAD_TOKENS_PARTY_CAP,
    NARRATOR_PAYLOAD_TOKENS_TYPICAL,
    tokens,
)
from performance.perf_harness import (
    CountingNarrator,
    ScenarioExtractor,
    build_journey,
    interrupt_payload,
    journey_message,
    many_rides,
)

UNGROUNDED_TEXT = "We found 99 attractions for you."
MESSAGE = journey_message("Famaa", 2, "TRON")


class RepairingExtractor(ScenarioExtractor):
    """Fails schema validation ``failures`` times, then extracts properly."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self._failures = failures

    def extract(self, human_messages, repair_hint=None):  # type: ignore[no-untyped-def]
        valid = super().extract(human_messages, repair_hint)
        if self.calls <= self._failures:
            return {**valid, "unexpected_field": "x"}
        return valid


def test_a_plan_costs_one_extractor_call_and_one_narrator_call() -> None:
    journey = build_journey(narrator=CountingNarrator())

    journey.run("t", MESSAGE)

    assert journey.extractor.calls == EXTRACTOR_CALLS_HAPPY_PATH
    assert journey.narrator.calls == NARRATOR_CALLS_HAPPY_PATH


def test_confirming_and_approving_make_no_further_model_calls() -> None:
    journey = build_journey(narrator=CountingNarrator())
    journey.start("t", MESSAGE)
    after_start = (journey.extractor.calls, journey.narrator.calls)

    journey.confirm("t")
    journey.approve("t")

    assert after_start[0] == EXTRACTOR_CALLS_HAPPY_PATH
    assert journey.extractor.calls == after_start[0]
    assert journey.narrator.calls == NARRATOR_CALLS_HAPPY_PATH


def test_an_ungrounded_explanation_is_retried_once_and_then_replaced_by_the_template() -> (
    None
):
    journey = build_journey(narrator=CountingNarrator(UNGROUNDED_TEXT))

    journey.run("t", MESSAGE)

    assert journey.narrator.calls == NARRATOR_CALLS_REPAIR_PATH
    assert journey.active_plan("t") is not None
    explanation = journey.proposals()[0][1].explanation
    assert "99" not in explanation


def test_a_grounded_retry_stops_after_the_second_narrator_call() -> None:
    narrator = CountingNarrator(UNGROUNDED_TEXT, "Here is your plan in order.")
    journey = build_journey(narrator=narrator)

    journey.run("t", MESSAGE)

    assert narrator.calls == NARRATOR_CALLS_REPAIR_PATH


def test_a_schema_invalid_extraction_is_repaired_with_one_more_call() -> None:
    extractor = RepairingExtractor(failures=1)
    journey = build_journey(extractor=extractor, narrator=CountingNarrator())

    result = journey.start("t", MESSAGE)

    assert extractor.calls == EXTRACTOR_CALLS_REPAIR_PATH
    assert (interrupt_payload(result) or {}).get(
        "kind"
    ) == "hard_constraint_confirmation"


def test_a_persistently_invalid_extraction_stops_at_the_repair_cap_per_turn() -> None:
    extractor = RepairingExtractor(failures=100)
    journey = build_journey(extractor=extractor, narrator=CountingNarrator())

    journey.start("t", MESSAGE)

    assert extractor.calls == EXTRACTOR_CALLS_REPAIR_PATH
    assert journey.narrator.calls == 0


def test_the_narrator_payload_of_a_typical_plan_is_within_the_token_budget() -> None:
    journey = build_journey(narrator=CountingNarrator())

    journey.run("t", MESSAGE)

    size = tokens(journey.narrator.payloads[0])
    assert size < NARRATOR_PAYLOAD_TOKENS_TYPICAL, (
        f"{size} tokens (budget {NARRATOR_PAYLOAD_TOKENS_TYPICAL})"
    )


def test_the_narrator_payload_of_a_twelve_must_do_plan_is_within_the_token_budget() -> (
    None
):
    names = [f"Ride Number {n:02d}" for n in range(12)]
    journey = build_journey(
        extractor=ScenarioExtractor(names),
        narrator=CountingNarrator(),
        attractions=many_rides(14),
    )

    journey.start("t", MESSAGE)
    journey.confirm("t")

    assert journey.narrator.calls == NARRATOR_CALLS_HAPPY_PATH
    size = tokens(journey.narrator.payloads[0])
    assert size < NARRATOR_PAYLOAD_TOKENS_MUST_DO_12, (
        f"{size} tokens (budget {NARRATOR_PAYLOAD_TOKENS_MUST_DO_12})"
    )


def test_the_narrator_payload_of_a_party_at_the_size_cap_is_within_the_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_PARTY_SIZE", 30)
    journey = build_journey(narrator=CountingNarrator())

    journey.start("t", journey_message("Famaa", 30, "TRON"))
    journey.confirm("t")

    assert journey.narrator.calls == NARRATOR_CALLS_HAPPY_PATH
    assert journey.values("t")["constraints"].party_size == 30
    size = tokens(journey.narrator.payloads[0])
    assert size < NARRATOR_PAYLOAD_TOKENS_PARTY_CAP, (
        f"{size} tokens (budget {NARRATOR_PAYLOAD_TOKENS_PARTY_CAP})"
    )
