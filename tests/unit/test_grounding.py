"""The deterministic numeral check behind EXPLAIN (P0-31 Done-when: "No numerical value
can be introduced unless present in the supplied data")."""

import json
from datetime import timedelta
from pathlib import Path

import factories
import pytest

from parkmind.agents.grounding import (
    mislabelled_preferences,
    numerals,
    ungrounded_numerals,
)
from parkmind.core.contracts import CheckResult, PlanDiff
from parkmind.services.use_cases.explain_plan import (
    explain_plan,
    explain_replan,
    explanation_payload,
)

PAYLOAD = (
    '{"name": "Space Mountain", "arrives": "14:05", "wait_minutes": 25, "value": 0.8}'
)


def test_numbers_clock_times_and_number_words_are_numerals():
    assert numerals("Arrive at 9:05, wait 25 min (0.80 match), two rides") == {
        "9:05",
        "25",
        "0.8",
        "2",
    }


def test_a_number_word_in_a_stop_name_is_grounded_by_the_payload():
    payload = '{"name": "Seven Dwarfs Mine Train"}'

    assert ungrounded_numerals("Seven Dwarfs Mine Train next.", payload) == []
    assert ungrounded_numerals("Seven Dwarfs, then four more rides.", payload) == ["4"]


@pytest.mark.parametrize(

    ("text", "language", "value"),
    [
        ("a forty minute wait", "en", "40"),
        ("twenty-five minutes", "en", "25"),
        ("twenty five minutes", "en", "25"),
        ("twelve stops", "en", "12"),
        ("ten stops", "en", "10"),
        # A number inside a hyphenated modifier is still a number.
        ("a forty-minute wait", "en", "40"),
        ("a ten-minute walk", "en", "10"),
        ("a twenty-five-minute walk", "en", "25"),
        ("two hundred steps", "en", "200"),
        ("two hundred and five steps", "en", "205"),
        ("veinticinco minutos", "es", "25"),
        ("treinta y cinco minutos", "es", "35"),
        ("dieciséis paradas", "es", "16"),
        ("veintiún minutos", "es", "21"),
        ("veintiuna personas", "es", "21"),
        ("ciento veinte minutos", "es", "120"),
        ("doscientos pasos", "es", "200"),
        ("once paradas", "es", "11"),
    ],
)
def test_a_number_spelled_out_is_not_grounded_unless_its_value_is_in_the_payload(
    text, language, value
):
    assert ungrounded_numerals(text, '{"wait_minutes": 15}', language) == [value]
    assert ungrounded_numerals(text, f'{{"wait_minutes": {value}}}', language) == []


def test_hundreds_are_one_number_so_a_part_cannot_ground_the_whole():
    """An invented 120 is not grounded by a real 20, nor 200 by a 2 and a 100."""
    assert ungrounded_numerals("ciento veinte minutos", '{"wait": 20}', "es") == ["120"]
    assert ungrounded_numerals("two hundred steps", '{"a": 2, "b": 100}') == ["200"]


@pytest.mark.parametrize(
    ("text", "language", "value"),
    [
        ("two thousand steps", "en", "2000"),
        ("two thousand five hundred steps", "en", "2500"),
        ("two thousand and five steps", "en", "2005"),
        ("a thousand steps", "en", "1000"),
        ("a million steps", "en", "1000000"),
        ("dos mil doscientos pasos", "es", "2200"),
        ("mil pasos", "es", "1000"),
        ("dos millones trescientos mil pasos", "es", "2300000"),
    ],
)
def test_thousands_and_millions_are_one_number_so_a_part_cannot_ground_the_whole(
    text, language, value
):
    """An invented 2000 is not grounded by a real 2 (two guests, say)."""
    assert numerals(text, language) == {value}
    assert ungrounded_numerals(text, '{"guests": 2}', language) == [value]


@pytest.mark.parametrize(
    "text",
    ["twenty, five", "wait: twenty. Five minutes", "twenty 3 five", "twenty 9:05 five"],
)
def test_punctuation_digits_and_times_end_a_run_of_number_words(text):
    """So a payload can't assemble a 25 from words that stand apart."""
    assert "25" not in numerals(text)
    assert {"20", "5"} <= numerals(text)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("one of your must-dos", "en"),
        ("your must-do rides", "en"),
        ("once you arrive", "en"),
        ("a well-known ride", "en"),
        ("half an hour", "en"),
        ("the dos and don'ts", "en"),
        ("una atracción", "es"),
        ("ten cuidado con el sol", "es"),
        ("ten en cuenta el calor", "es"),
    ],
)
def test_articles_pronouns_hyphenated_words_and_the_other_languages_numbers_are_not_numbers(
    text, language
):
    assert numerals(text, language) == set()


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


# --- the stated/learned label check (review #90, point 3) ---------------------------

LABELLED = {"0.8": "stated", "0.33": "learned"}


def test_labels_that_match_their_source_pass():
    text = "Thrill matters to you, 0.8 (stated); waiting weighs 0.33, which we learned."

    assert mislabelled_preferences(text, LABELLED) == []


def test_a_wrong_or_missing_label_is_caught():
    assert mislabelled_preferences("Waiting weighs 0.33 (stated).", LABELLED) == [
        "0.33 is learned, not stated"
    ]
    assert mislabelled_preferences("Thrill weighs 0.8.", LABELLED) == [
        "0.8 needs its label (stated)"
    ]


def test_an_ambiguous_figure_is_not_checked():
    """0.8 is also a stop's preference score here: the number alone can't tell which."""
    assert mislabelled_preferences("Thrill weighs 0.8.", LABELLED, {"0.8"}) == []


def test_figures_unrelated_to_preferences_need_no_label():
    assert mislabelled_preferences("A 20 minute wait at 10:33.", LABELLED) == []


LABEL_CASES = FIXTURE["label_cases"]


@pytest.mark.parametrize("case", LABEL_CASES, ids=[c["name"] for c in LABEL_CASES])
def test_fixture_label_cases_get_exactly_the_expected_verdict(case):
    payload = FIXTURE["payload"]
    labelled = {
        str(p["value"]): p["source"]
        for p in payload["preferences"]
        if p["source"] in ("stated", "learned")
    }
    others = numerals(json.dumps({**payload, "preferences": []}))

    assert (
        mislabelled_preferences(case["output"], labelled, set(labelled) & others)
        == case["label_errors"]
    )


# --- review #90: fractions, full-width digits, 12-hour times -----------------------

# A realistic payload: 3 stops, a 3 minute wait, a 0.5 weight, 45 and 25 minute waits.
REALISTIC = json.dumps(
    {
        "total_stops": 3,
        "stops": [
            {"name": "A", "arrives": "09:05", "wait_minutes": 3},
            {"name": "B", "arrives": "10:00", "wait_minutes": 45},
            {"name": "C", "arrives": "11:00", "wait_minutes": 25},
        ],
        "preferences": [{"weight": "lambda_queue", "value": 0.5, "source": "stated"}],
    }
)


def test_a_vulgar_fraction_is_a_numeral_by_its_value():
    assert numerals("a ½ hour wait") == {"0.5"}
    assert ungrounded_numerals("a ½ hour wait", PAYLOAD) == ["0.5"]


def test_full_width_digits_are_ordinary_digits():
    assert numerals("at 14:０５, a ２５ minute wait") == {"14:05", "25"}
    assert ungrounded_numerals("at 14:０５, a ２５ minute wait", PAYLOAD) == []


def test_a_12_hour_time_is_rejected_only_when_its_number_is_not_in_the_payload():
    assert ungrounded_numerals("Meet at 3 PM.", '{"arrives": "15:00"}') == ["3"]
    # Known gap: with a 3 elsewhere in the payload (3 stops) it is read as that 3.
    assert ungrounded_numerals("Meet at 3 PM.", REALISTIC) == []


def test_a_fraction_passes_when_the_payload_has_that_value():
    """Known gap: the check compares values, not how they were written."""
    assert ungrounded_numerals("a ½ weight", REALISTIC) == []


def test_swapped_figures_pass_when_both_are_in_the_payload():
    """Known gap: a subset check cannot tell which stop the 45 belongs to."""
    text = "B has a 25 minute wait, C a 45 minute wait"

    assert ungrounded_numerals(text, REALISTIC) == []


def test_grouped_thousands_and_exponents_are_read_as_separate_numbers():
    assert numerals("1,500 steps") == {"1", "500"}
    assert numerals("1e3") == {"1", "3"}
    assert ungrounded_numerals("1,500 steps", REALISTIC) == ["1", "500"]
    # Known gap: pieces that are already in the payload pass.
    assert ungrounded_numerals("1e3 steps", REALISTIC) == ["1"]
    assert ungrounded_numerals("a 3,25 wait", REALISTIC) == []


# --- default weights, stop scores and lists (review #90 follow-ups) ----------------

WEIGHTS = {"0.8": "stated", "0.33": "learned", "0.5": "default"}


def test_a_default_weight_called_stated_or_learned_is_caught():
    assert mislabelled_preferences("Walking weighs 0.5 (stated).", WEIGHTS) == [
        "0.5 is default, not stated"
    ]
    assert mislabelled_preferences(
        "Walking weighs 0.5, which we learned.", WEIGHTS
    ) == ["0.5 is default, not learned"]


def test_a_default_weight_without_a_label_is_fine():
    assert mislabelled_preferences("Walking weighs 0.5 by default.", WEIGHTS) == []
    assert mislabelled_preferences("Walking weighs 0.5.", WEIGHTS) == []


def test_a_stop_score_with_or_without_a_label_is_not_checked():
    """0.81 is a stop's preference_score, not a weight: it is never in the labels."""
    assert mislabelled_preferences("Space Mountain scores 0.81.", WEIGHTS) == []
    assert (
        mislabelled_preferences("Space Mountain scores 0.81 (stated).", WEIGHTS) == []
    )


def test_list_items_inherit_the_label_of_their_header():
    text = "Your stated priorities:\n- thrill 0.8\n- more thrill 0.33\nDone."

    assert mislabelled_preferences(text, WEIGHTS) == ["0.33 is learned, not stated"]
    assert (
        mislabelled_preferences("Your stated priorities:\n- thrill 0.8", WEIGHTS) == []
    )


def test_a_list_item_with_its_own_label_keeps_it():
    text = "Your priorities:\n- thrill 0.8 (stated)\n- queues 0.33 (learned)"

    assert mislabelled_preferences(text, WEIGHTS) == []


def test_a_list_without_a_labelled_header_still_needs_labels():
    assert mislabelled_preferences("Priorities:\n- thrill 0.8", WEIGHTS) == [
        "0.8 needs its label (stated)"
    ]
