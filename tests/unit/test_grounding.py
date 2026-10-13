"""The deterministic numeral check behind EXPLAIN (P0-31 Done-when: "No numerical value
can be introduced unless present in the supplied data")."""

import json
from datetime import timedelta
from pathlib import Path

import factories
import pytest

from parkmind.agents.grounding import numerals, ungrounded_numerals
from parkmind.core.contracts import CheckResult, PlanDiff
from parkmind.services.use_cases.explain_plan import (
    explain_plan,
    explain_replan,
    explanation_payload,
)

PAYLOAD = (
    '{"name": "Space Mountain", "arrives": "14:05", "wait_minutes": 25, "value": 0.8}'
)


def test_numbers_and_clock_times_are_numerals_and_words_are_not():
    assert numerals("Arrive at 9:05, wait 25 min (0.80 match), two rides") == {
        "9:05",
        "25",
        "0.8",
    }


def test_the_same_value_written_differently_is_one_numeral():
    assert numerals("25.0 at 09:05") == numerals("25 at 9:05") == {"25", "9:05"}


def test_digits_against_letters_still_count():
    assert numerals("a 15min walk") == {"15"}


def test_a_grounded_text_passes():
    text = "Space Mountain at 14:05: a 25 minute wait, a 0.8 thrill match."

    assert ungrounded_numerals(text, PAYLOAD) == []


def test_an_invented_figure_is_caught():
    """Sebastián's case in #90: a wait the data never gave."""
    text = "Space Mountain at 14:05 with a 30 minute wait."

    assert ungrounded_numerals(text, PAYLOAD) == ["30"]


def test_a_bare_hour_is_not_grounded_by_a_clock_time():
    """#90 allowed every stop's hour as a number; the subset rule does not."""
    assert ungrounded_numerals("Head over around 14.", PAYLOAD) == ["14"]
    assert ungrounded_numerals("at 14:30", PAYLOAD) == ["14:30"]


def test_a_rounded_figure_is_not_grounded_by_the_precise_one():
    assert ungrounded_numerals("about 1 match", PAYLOAD) == ["1"]


# --- the template fallback is always grounded --------------------------------------

ADULT = factories.guest(guest_id="g1", height_cm=175)
CHILD = factories.guest(guest_id="g2", role="child", height_cm=90)


def _day():
    start = factories.stop().arrival_time
    return factories.plan(
        stops=[
            factories.stop(
                node_id="a1",
                arrival_time=start,
                departure_time=start + timedelta(minutes=47),
                expected_wait_minutes=27.5,
                walking_minutes=3.4,
                served_guests=["g1"],
            ),
            factories.stop(
                node_id="a2",
                arrival_time=start + timedelta(minutes=52),
                departure_time=start + timedelta(minutes=90),
                expected_wait_minutes=12.6,
                walking_minutes=4.5,
            ),
        ],
        total_wait_minutes=40.1,
        total_walking_minutes=7.9,
        unmet_must_do=["id-tron"],
    )


NAMES = {"a1": "Space Mountain", "a2": "Big Thunder", "id-tron": "TRON"}


@pytest.mark.parametrize("replan", [False, True], ids=["initial", "replan"])
def test_the_template_never_says_a_figure_its_payload_lacks(replan: bool):
    plan, check = _day(), CheckResult(valid=True)
    common = {"guests": [ADULT, CHILD], "height_limits": {"a1": 112}}
    reason = "Big Thunder Mountain closed; the party is 18 min behind schedule."
    diff = PlanDiff(stops_removed=["id-tron"], stops_added=["a2"])

    if replan:
        text = explain_replan(plan, check, NAMES, reason=reason, diff=diff, **common)
        payload = explanation_payload(
            plan, check, NAMES, must_do=["a1"], reason=reason, diff=diff, **common
        )
    else:
        text = explain_plan(plan, check, NAMES, **common)
        payload = explanation_payload(plan, check, NAMES, must_do=["a1"], **common)

    # The template also shows the guests their own heights, which the LLM never gets.
    heights = " ".join(f"{g.height_cm:g}" for g in (ADULT, CHILD))
    assert ungrounded_numerals(text, payload.to_json() + " " + heights) == []
    # ...and only that: the child who sits Space Mountain out.
    assert ungrounded_numerals(text, payload.to_json()) == ["90"]


def test_a_colon_after_a_time_is_punctuation_but_seconds_are_not_a_time():
    assert numerals("Space Mountain at 14:05: then lunch") == {"14:05"}
    assert numerals("at 14:05:30") == {"14", "5", "30"}


# --- the fixture behind the Done-when -----------------------------------------------

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "explain"
        / "grounded_cases.json"
    ).read_text(encoding="utf-8")
)
CASES = FIXTURE["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_outputs_get_exactly_the_expected_verdict(case):
    payload = json.dumps(FIXTURE["payload"], ensure_ascii=False, indent=1)

    assert ungrounded_numerals(case["output"], payload) == case["ungrounded"]
