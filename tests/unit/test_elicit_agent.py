"""``elicit`` node: extraction recovery, missing information, contracts."""

from datetime import date, datetime

import pytest
from elicit_support import (
    FakeExtractor,
    load_scenarios,
    make_intake,
    make_names,
    scenario,
)
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from parkmind.agents.elicit_agent import (
    UNAVAILABLE_FLAG,
    AnthropicExtractor,
    ExtractorOutputError,
    ExtractorUnavailableError,
    build_guest_profiles,
    build_party_constraints,
    extract_with_recovery,
    make_elicit_node,
    missing_information,
    pending_confirmations,
    ungrounded_heights,
)
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.config.settings import settings
from parkmind.core.contracts import (
    PARK_TZ,
    Attraction,
    AttractionCategory,
    PreferenceSource,
    SensitivityKind,
    SensitivityLevel,
)
from parkmind.services.use_cases.confirmation_echo import echo_for

MAX_PARTY_SIZE = settings.MAX_PARTY_SIZE
TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=PARK_TZ)
COMPLETE = scenario("wiki_example_complete")
MISSING_DEPARTURE = scenario("wiki_example_missing_departure")
BAD_OUTPUT = {"party_size": 9, "guests": [{"role": "adult"}]}


def _extraction(s: dict) -> ElicitationExtraction:
    return ElicitationExtraction.model_validate(s["extraction"])


def _state(*texts: str) -> dict:
    return {"thread_id": "t1", "messages": [HumanMessage(content=t) for t in texts]}


def _node(extractor: FakeExtractor):
    intake, store = make_intake()
    node = make_elicit_node(
        extractor, intake, make_names(), today=lambda: TODAY, now=lambda: NOW
    )
    return node, intake, store


# --- schema-validation failure is recoverable ---------------------------------


def test_invalid_output_is_retried_with_a_repair_hint_and_then_accepted() -> None:
    extractor = FakeExtractor(BAD_OUTPUT, COMPLETE["extraction"])

    outcome = extract_with_recovery(extractor, ["hi"])

    assert outcome.extraction is not None and outcome.attempts == 2
    first_hint, second_hint = extractor.calls[0][1], extractor.calls[1][1]
    assert first_hint is None
    assert second_hint is not None and "party_size" in second_hint


def test_repair_hint_never_contains_the_guests_words() -> None:
    bad = {
        "guests": [{"role": "adult"}],
        "accessibility": [{"guest_ref": 1, "label": "my secret"}],
    }
    extractor = FakeExtractor(bad, COMPLETE["extraction"])

    extract_with_recovery(extractor, ["hi"])

    assert "my secret" not in (extractor.calls[1][1] or "")


def test_missing_tool_call_is_retried_like_a_schema_failure() -> None:
    extractor = FakeExtractor(ExtractorOutputError("no output"), COMPLETE["extraction"])

    assert extract_with_recovery(extractor, ["hi"]).extraction is not None


def test_exhausted_retries_return_no_extraction_and_do_not_raise() -> None:
    outcome = extract_with_recovery(FakeExtractor(BAD_OUTPUT), ["hi"], max_attempts=3)

    assert outcome.extraction is None
    assert outcome.attempts == 3 and outcome.errors


def test_unreadable_output_asks_the_guests_to_rephrase() -> None:
    node, intake, _ = _node(FakeExtractor(BAD_OUTPUT))

    update = node(_state("blah"))

    assert update["constraints"] is None and update["constraints_valid"] is False
    assert update["pending_hard_constraint_confirmation"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["unreadable_response"]
    assert intake.pending_guest_ids("t1") == []


# --- missing required information is surfaced ----------------------------------


def test_missing_departure_time_is_reported_and_asked() -> None:
    assert missing_information(_extraction(MISSING_DEPARTURE)) == ["departure_time"]

    node, _, _ = _node(FakeExtractor(MISSING_DEPARTURE["extraction"]))
    update = node(_state(*MISSING_DEPARTURE["messages"]))

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["departure_time"]
    assert "leave" in message.content.lower()


def test_unknown_guest_role_is_reported() -> None:
    s = scenario("unknown_adult_or_child")

    assert missing_information(_extraction(s)) == ["guest_role:g2"]


def test_no_guests_is_reported() -> None:
    assert missing_information(ElicitationExtraction()) == ["guests", "departure_time"]


# --- representative scenarios produce valid contracts ---------------------------


def test_wiki_example_builds_party_constraints() -> None:
    extraction = _extraction(COMPLETE)
    resolution = make_names().resolve(extraction.must_do)
    constraints = build_party_constraints(
        extraction, today=TODAY, resolution=resolution
    )

    assert constraints.party_size == 4
    assert [g.guest_id for g in constraints.guests] == ["g1", "g2", "g3", "g4"]
    assert constraints.must_do == ["id-tron", "id-space"]
    assert constraints.lunch_window is not None
    assert (
        constraints.lunch_window.start.hour,
        constraints.lunch_window.start.minute,
    ) == (12, 30)
    assert (constraints.lunch_window.end.hour, constraints.lunch_window.end.minute) == (
        13,
        30,
    )
    assert constraints.departure_time == datetime(2026, 10, 6, 20, 0, tzinfo=PARK_TZ)


def test_soft_preferences_become_stated_guest_profile_values() -> None:
    profiles = build_guest_profiles(_extraction(COMPLETE), now=NOW)

    assert [p.guest_id for p in profiles] == ["g1", "g2", "g3", "g4"]
    kid = profiles[2]
    assert kid.sensitivities == {SensitivityKind.INTENSITY: SensitivityLevel.LOW}
    assert profiles[0].sensitivities == {}
    # Nothing was stated about queues: a default, not an invented preference.
    assert kid.queue_tolerance.source == PreferenceSource.DEFAULT
    assert kid.queue_tolerance.confidence == 0.0


def test_stated_tolerance_is_marked_stated() -> None:
    profiles = build_guest_profiles(
        _extraction(scenario("casual_rest_breaks_vs_queue_preference")), now=NOW
    )

    assert profiles[0].queue_tolerance.source == PreferenceSource.STATED
    assert profiles[0].queue_tolerance.stated_value == 0.2


# --- accessibility is not silently stored as a soft preference ------------------


def test_accessibility_never_appears_in_guest_profiles() -> None:
    profiles = build_guest_profiles(_extraction(COMPLETE), now=NOW)
    dumped = " ".join(p.model_dump_json() for p in profiles)

    assert "LIMITED_WALKING" not in dumped
    walking_father = profiles[1]
    assert walking_father.walking_tolerance.source == PreferenceSource.DEFAULT


def test_accessibility_statement_is_staged_and_listed_as_a_token_only() -> None:
    node, intake, store = _node(FakeExtractor(COMPLETE["extraction"]))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints_valid"] is False
    pending = update["pending_hard_constraint_confirmation"]
    assert "accessibility:g2" in pending
    assert not any("LIMITED_WALKING" in entry or "walk" in entry for entry in pending)
    assert intake.pending_guest_ids("t1") == ["g2"]
    assert store.puts == 0  # nothing reaches the store before confirmation
    assert "accessibility_ref" not in update


@pytest.mark.parametrize(
    "case",
    [s for s in load_scenarios() if not s["expect_missing"]],
    ids=lambda s: s["id"],
)
def test_every_hard_item_is_pending(case: dict) -> None:
    extraction = _extraction(case)
    resolution = make_names().resolve([*extraction.must_do, *extraction.avoid])
    assert pending_confirmations(extraction, resolution) == case["expect_pending"]


@pytest.mark.parametrize("case", load_scenarios(), ids=lambda s: s["id"])
def test_every_hard_labeled_item_in_the_fixtures_is_a_hard_classification(
    case: dict,
) -> None:
    classified = _extraction(case).classified_items()
    assert {
        k: v for k, v in case["expected"].items() if v == "hard"
    }.items() <= classified.items()


def test_a_stated_height_is_pending_and_never_reaches_the_checker_unconfirmed() -> None:
    case = scenario("stated_child_height")
    node, _, _ = _node(FakeExtractor(case["extraction"]))

    update = node(_state(*case["messages"]))

    assert "height:g2: 122" in update["pending_hard_constraint_confirmation"]
    assert update["constraints_valid"] is False


def test_a_height_is_echoed_in_cm_and_feet_so_the_guests_can_check_it() -> None:
    echo = echo_for("height:g2: 122")

    assert "Guest 2" in echo and "122 cm" in echo and "4'0\"" in echo
    assert "skipped" in echo


def test_no_stated_height_means_no_height_confirmation() -> None:
    extraction = _extraction(COMPLETE)
    resolution = make_names().resolve([*extraction.must_do, *extraction.avoid])

    assert not any(
        e.startswith("height:") for e in pending_confirmations(extraction, resolution)
    )


def test_a_new_extraction_replaces_stale_staging() -> None:
    node, intake, _ = _node(
        FakeExtractor(
            COMPLETE["extraction"],
            scenario("pure_preferences_no_accessibility")["extraction"],
        )
    )

    node(_state(*COMPLETE["messages"]))
    node(_state("we changed our minds"))

    assert intake.pending_guest_ids("t1") == []


def test_node_requires_a_guest_message() -> None:
    node, _, _ = _node(FakeExtractor(COMPLETE["extraction"]))

    with pytest.raises(ValueError):
        node({"thread_id": "t1", "messages": []})


def test_echo_is_plain_language() -> None:
    assert "must-do" in echo_for("must_do: TRON")
    assert "off-limits" in echo_for("avoid: Splash Mountain")
    assert "20:00" in echo_for("departure_time: 20:00")
    assert "12:30" in echo_for("lunch_window: 12:30-13:30")
    assert "180" in echo_for("walking_budget: 180")


def test_validation_error_type_is_what_is_retried() -> None:
    with pytest.raises(ValidationError):
        ElicitationExtraction.model_validate(BAD_OUTPUT)


# --- must-do / avoid names become catalog node ids, or go back to the human ------


def _with(extraction: dict, **changes) -> dict:
    return {**extraction, **changes}


def test_confirmed_names_become_node_ids_and_the_echo_shows_the_official_name() -> None:
    node, _, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], must_do=["tron"], avoid=["Splash"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"].must_do == ["id-tron"]
    assert update["constraints"].avoid == ["id-splash"]
    pending = update["pending_hard_constraint_confirmation"]
    assert "must_do: TRON" in pending and "avoid: Splash Mountain" in pending


def test_two_spoken_names_for_one_attraction_become_one_id() -> None:
    node, _, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], must_do=["TRON", "tron"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"].must_do == ["id-tron"]


def test_unknown_attraction_is_asked_not_stored() -> None:
    node, intake, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], avoid=["Death Star"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == [
        "unknown_attraction:Death Star"
    ]
    assert "Death Star" in message.content
    assert intake.pending_guest_ids("t1") == []


def test_ambiguous_attraction_lists_the_options() -> None:
    node, _, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], must_do=["Mountain"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == [
        "ambiguous_attraction:Mountain"
    ]
    for official in ("Space Mountain", "Splash Mountain", "Big Thunder Mountain"):
        assert official in message.content


def test_unresolved_names_are_asked_together_with_other_missing_information() -> None:
    node, _, _ = _node(
        FakeExtractor(_with(MISSING_DEPARTURE["extraction"], avoid=["Death Star"]))
    )

    update = node(_state(*MISSING_DEPARTURE["messages"]))

    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "departure_time",
        "unknown_attraction:Death Star",
    ]


def test_the_same_attraction_as_must_do_and_avoid_is_asked_not_planned() -> None:
    node, intake, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], avoid=["TRON"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == [
        "conflicting_attraction:TRON"
    ]
    assert (
        "TRON" in message.content
        and "must-do" in message.content
        and "avoid" in message.content
    )
    assert intake.pending_guest_ids("t1") == []


def test_a_conflict_is_found_even_when_the_two_lists_use_different_spoken_names() -> (
    None
):
    node, _, _ = _node(
        FakeExtractor(_with(COMPLETE["extraction"], must_do=["tron"], avoid=["TRON"]))
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "conflicting_attraction:TRON"
    ]


def test_different_attractions_in_must_do_and_avoid_are_not_a_conflict() -> None:
    node, _, _ = _node(FakeExtractor(_with(COMPLETE["extraction"], avoid=["Splash"])))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is not None


def test_a_bad_time_is_repaired_without_echoing_what_the_guests_said() -> None:
    bad = _with(COMPLETE["extraction"], departure_time="around 1pm for sure")
    extractor = FakeExtractor(bad, COMPLETE["extraction"])

    outcome = extract_with_recovery(extractor, ["hi"])

    hint = extractor.calls[1][1] or ""
    assert outcome.extraction is not None
    assert "departure_time" in hint and "HH:MM" in hint
    assert "around" not in hint and "1pm" not in hint


def test_an_empty_catalog_is_a_park_data_message_not_a_question_to_the_guests() -> None:
    intake, _ = make_intake()
    node = make_elicit_node(
        FakeExtractor(COMPLETE["extraction"]),
        intake,
        make_names([]),
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    assert update["messages"][0].additional_kwargs[UNAVAILABLE_FLAG]
    assert "park information" in update["messages"][0].content


def test_an_unreachable_model_is_a_flagged_message_not_a_raw_error() -> None:
    intake, _ = make_intake()
    node = make_elicit_node(
        FakeExtractor(ExtractorUnavailableError("ConnectError")),
        intake,
        make_names(),
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    assert update["messages"][0].additional_kwargs[UNAVAILABLE_FLAG]
    assert "ConnectError" not in update["messages"][0].content


def test_a_failing_model_call_is_reported_as_unavailable_without_the_connection_details() -> (
    None
):
    class Down:
        def invoke(self, *_: object) -> object:
            raise ConnectionError("https://user:secret@llm.internal:443 refused")

    extractor = AnthropicExtractor()
    extractor._runnable = lambda: Down()  # type: ignore[method-assign]

    with pytest.raises(ExtractorUnavailableError) as raised:
        extractor.extract(["hi"])

    assert str(raised.value) == "ConnectionError"
    assert "secret" not in str(raised.value)


# --- a height must come from the guests' own words ------------------------------


def _with_heights(*heights: float | None) -> ElicitationExtraction:
    return ElicitationExtraction.model_validate(
        {
            "guests": [
                {"role": "adult" if i == 0 else "child", "height_cm": h}
                for i, h in enumerate(heights)
            ],
            "departure_time": "18:00",
        }
    )


@pytest.mark.parametrize(
    "text, height",
    [
        ("my son is 122 cm", 122),
        ("he is 1.22 m tall", 122),
        ("she is 1,70 m", 170),
        ("about 4 feet tall", 122),
        ("he is 4'6\" now", 137),
        ("5 ft 10", 178),
        ("he is 122", 122),
    ],
)
def test_a_height_the_guests_wrote_is_grounded(text: str, height: float) -> None:
    assert ungrounded_heights(_with_heights(None, height), [text]) == []


def test_a_height_clamped_into_the_schema_range_is_not_grounded() -> None:
    # "400 cm and 20 cm" came back as 200 and 200: both fit the schema, neither was said.
    extraction = _with_heights(200, 200)

    assert ungrounded_heights(extraction, ["Two guests, 400 cm and 20 cm."]) == [
        "height_unverified:g1",
        "height_unverified:g2",
    ]


def test_a_height_nobody_stated_is_not_grounded() -> None:
    assert ungrounded_heights(
        _with_heights(None, 120), ["Me and my son, leaving at 6 PM"]
    ) == ["height_unverified:g2"]


def test_durations_and_counts_are_not_mistaken_for_heights() -> None:
    text = "2 adults, 120 minutes of walking at most, 100 % sure, leaving at 18:00"

    assert ungrounded_heights(_with_heights(None, 120), [text]) == [
        "height_unverified:g2"
    ]


def test_a_height_stated_in_an_earlier_message_still_counts() -> None:
    extraction = _with_heights(None, 122)

    assert ungrounded_heights(extraction, ["my son is 122 cm", "we leave at 6"]) == []


def test_an_ungrounded_height_asks_instead_of_planning() -> None:
    extraction = _with_heights(200, 200).model_dump(mode="json")
    node, _, _ = _node(FakeExtractor(extraction))

    update = node(_state("Two guests, 400 cm and 20 cm. Leaving at 6 PM."))

    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "height_unverified:g1",
        "height_unverified:g2",
    ]
    assert "How tall is Guest 1, in cm" in update["messages"][0].content


def test_a_grounded_height_goes_to_confirmation_as_before() -> None:
    extraction = _with_heights(None, 122).model_dump(mode="json")
    node, _, _ = _node(FakeExtractor(extraction))

    update = node(_state("Me and my son, he is 122 cm. Leaving at 6 PM."))

    assert "height:g2: 122" in update["pending_hard_constraint_confirmation"]


# --- guests named by how the guests call them ---------------------------------


def _named(*labels: str | None) -> ElicitationExtraction:
    return ElicitationExtraction.model_validate(
        {
            "guests": [{"role": "adult", "label": label} for label in labels],
            "departure_time": "18:00",
        }
    )


def test_a_label_reaches_the_guest_contract() -> None:
    extraction = _named("tu mamá", None)
    resolution = make_names().resolve([])

    guests = build_party_constraints(
        extraction, today=TODAY, resolution=resolution
    ).guests

    assert [g.label for g in guests] == ["tu mamá", None]


def test_the_height_echo_uses_the_label_and_falls_back_to_the_number() -> None:
    labels = {"g2": "tu hija"}

    assert echo_for("height:g2: 110", "es", labels=labels).startswith(
        "Tu hija mide 110 cm"
    )
    assert echo_for("height:g2: 110", "en", labels={"g2": "your daughter"}).startswith(
        "Your daughter is 110 cm"
    )
    assert echo_for("height:g1: 110", "es", labels=labels).startswith(
        "La Persona 1 mide"
    )


def test_the_unverified_height_question_uses_the_label() -> None:
    from parkmind.agents.elicit_agent import _question_for

    question = _question_for(["height_unverified:g2"], None, "es", 2, [None, "tu hija"])

    assert "de tu hija" in question and "Persona" not in question


@pytest.mark.parametrize(
    "label", ["", "   ", "Guest 2", "mi mamá de 62 años", "x" * 31]
)
def test_a_label_that_is_not_a_short_name_is_dropped(label: str) -> None:
    assert _named(label).guests[0].label is None


def test_a_label_is_trimmed() -> None:
    assert _named("  tu   mamá ").guests[0].label == "tu mamá"


# --- heights are asked when a must-do has a minimum height --------------------------


def _height_catalog() -> list[Attraction]:
    return [
        Attraction(
            node_id="id-space",
            name="Space Mountain",
            category=AttractionCategory.THRILL,
            height_restriction_cm=112,
            typical_wait_minutes=40,
        ),
        Attraction(
            node_id="id-dumbo",
            name="Dumbo",
            category=AttractionCategory.FAMILY,
            height_restriction_cm=None,
            typical_wait_minutes=20,
        ),
    ]


def _height_node(extractor: FakeExtractor):
    intake, _ = make_intake()
    return make_elicit_node(
        extractor,
        intake,
        make_names(_height_catalog()),
        today=lambda: TODAY,
        now=lambda: NOW,
    )


def _party(*heights: float | None, must_do: list[str]) -> dict:
    return {
        "guests": [
            {"role": "adult" if i == 0 else "child", "height_cm": h}
            for i, h in enumerate(heights)
        ],
        "must_do": must_do,
        "departure_time": "18:00",
    }


def test_a_must_do_with_a_minimum_height_asks_for_the_heights_nobody_gave() -> None:
    node = _height_node(FakeExtractor(_party(None, None, must_do=["Space Mountain"])))

    update = node(
        _state("A parent and a child. We must ride Space Mountain, leaving at 6 PM.")
    )

    message = update["messages"][0]
    assert update["constraints"] is None
    assert message.additional_kwargs["missing_information"] == [
        "height_needed:Space Mountain"
    ]
    assert "Space Mountain" in message.content and "112 cm" in message.content
    assert "How tall" in message.content


def test_one_question_covers_every_must_do_with_a_minimum_height() -> None:
    catalog = [
        *_height_catalog(),
        Attraction(
            node_id="id-tron",
            name="TRON",
            category=AttractionCategory.THRILL,
            height_restriction_cm=122,
            typical_wait_minutes=30,
        ),
    ]
    intake, _ = make_intake()
    node = make_elicit_node(
        FakeExtractor(_party(None, None, must_do=["Space Mountain", "TRON"])),
        intake,
        make_names(catalog),
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    update = node(_state("Two of us, Space Mountain and TRON, leaving at 6 PM."))

    message = update["messages"][0]
    assert len(message.additional_kwargs["missing_information"]) == 2
    assert message.content.count("How tall") == 1
    assert "112 cm" in message.content and "122 cm" in message.content


def test_the_heights_question_is_in_the_guests_language() -> None:
    node = _height_node(FakeExtractor(_party(None, None, must_do=["Space Mountain"])))

    update = node(
        _state("Somos dos, queremos Space Mountain. Salimos a las 6 de la tarde.")
    )

    assert "mínima" in update["messages"][0].content
    assert "cuánto mide" in update["messages"][0].content.lower()


def test_a_solo_guest_is_asked_in_the_singular() -> None:
    node = _height_node(FakeExtractor(_party(None, must_do=["Space Mountain"])))

    update = node(_state("I am an adult and I want Space Mountain. I leave at 6 PM."))

    assert "How tall are you" in update["messages"][0].content


def test_no_height_is_asked_when_every_guest_gave_one() -> None:
    node = _height_node(FakeExtractor(_party(170, 130, must_do=["Space Mountain"])))

    update = node(
        _state("Adult 170 cm and kid 130 cm. Space Mountain. Leaving at 6 PM.")
    )

    assert update["constraints"] is not None


def test_no_height_is_asked_when_no_must_do_has_a_minimum_height() -> None:
    node = _height_node(FakeExtractor(_party(None, None, must_do=["Dumbo"])))

    update = node(_state("A parent and a child. We want Dumbo. Leaving at 6 PM."))

    assert update["constraints"] is not None


def test_a_height_restricted_avoid_does_not_ask_for_heights() -> None:
    party = {**_party(None, None, must_do=[]), "avoid": ["Space Mountain"]}
    node = _height_node(FakeExtractor(party))

    update = node(
        _state("A parent and a child. Avoid Space Mountain. Leaving at 6 PM.")
    )

    assert update["constraints"] is not None


def test_a_guest_who_does_not_answer_is_asked_only_once() -> None:
    """The checker keeps failing closed for the guests who never gave a height."""
    node = _height_node(FakeExtractor(_party(None, None, must_do=["Space Mountain"])))
    first = node(_state("A parent and a child. Space Mountain. Leaving at 6 PM."))
    asked = first["messages"][0]

    second = node(
        {
            "thread_id": "t1",
            "messages": [
                HumanMessage(
                    content="A parent and a child. Space Mountain. Leaving at 6 PM."
                ),
                asked,
                HumanMessage(content="I don't know their heights"),
            ],
        }
    )

    assert second["constraints"] is not None
    assert {g.height_cm for g in second["constraints"].guests} == {None}


def test_the_heights_that_are_missing_are_asked_alongside_other_missing_information() -> (
    None
):
    party = {**_party(None, None, must_do=["Space Mountain"]), "departure_time": None}
    node = _height_node(FakeExtractor(party))

    update = node(_state("A parent and a child. Space Mountain."))

    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "departure_time",
        "height_needed:Space Mountain",
    ]


# --- a party over the limit is asked to be smaller --------------------------------


def _crowd(size: int) -> dict:
    return {
        "party_size": size,
        "guests": [{"role": "adult"} for _ in range(size)],
        "must_do": ["Dumbo"],
        "departure_time": "18:00",
    }


def test_a_party_over_the_limit_is_asked_to_be_smaller_and_nothing_else() -> None:
    node = _height_node(FakeExtractor(_crowd(30)))

    update = node(_state("We are 30 adults and want Dumbo. We leave at 6 PM."))

    message = update["messages"][0]
    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    assert message.additional_kwargs["missing_information"] == ["party_too_large:30"]
    assert str(MAX_PARTY_SIZE) in message.content and "30" in message.content


def test_a_party_at_the_limit_is_planned() -> None:
    node = _height_node(FakeExtractor(_crowd(MAX_PARTY_SIZE)))

    update = node(_state("We are a group and want Dumbo. We leave at 6 PM."))

    assert update["constraints"] is not None


def test_the_party_limit_message_is_in_the_guests_language() -> None:
    node = _height_node(FakeExtractor(_crowd(30)))

    update = node(
        _state("Somos 30 adultos y queremos Dumbo. Salimos a las 6 de la tarde.")
    )

    assert "máximo" in update["messages"][0].content.lower()


def test_the_prompt_makes_the_writer_guest_one_so_a_relative_is_guest_two() -> None:
    from parkmind.agents.elicit_prompt import ELICIT_SYSTEM_PROMPT

    assert "they are guest 1" in ELICIT_SYSTEM_PROMPT
    assert "`guest_ref` 2, never 1" in ELICIT_SYSTEM_PROMPT


# --- the walking limit: the guests' answer to the walking question ------------


WALKING_BASE = {
    "guests": [{"role": "adult"}, {"role": "adult"}],
    "party_size": 2,
    "departure_time": "18:00",
}
WALKING_FIRST = (
    "Somos 2 adultos, salimos a las 18:00 y podemos caminar como 2 km en total."
)
WALKING_VAGUE = "Somos 2 adultos, salimos a las 18:00 y mi papá camina poco."


def _walking_turns(node, *replies: str, first: str = WALKING_FIRST) -> list[dict]:
    """Play the guests' replies, each answering the question the node asked before it."""
    messages = [HumanMessage(content=first)]
    updates = []
    for reply in [None, *replies]:
        if reply is not None:
            messages.append(HumanMessage(content=reply))
        update = node({"thread_id": "t1", "messages": messages, "language": "es"})
        updates.append(update)
        messages.extend(update.get("messages") or [])
    return updates


def _asked(update: dict) -> list[str]:
    [question] = update["messages"]
    return question.additional_kwargs["missing_information"]


def test_a_bare_number_answering_the_walking_question_is_taken_as_minutes() -> None:
    """The model reads a limit nobody wrote; the guests answer the minutes question with '30'."""
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=60),
        dict(WALKING_BASE, party_walking_budget_minutes=30),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    first, answered = _walking_turns(node, "30", first=WALKING_VAGUE)

    assert "walking_budget_unverified" in _asked(first)
    assert "walking_budget: 30" in answered["pending_hard_constraint_confirmation"]


def test_the_guests_answer_wins_over_an_extraction_that_kept_an_old_reading() -> None:
    """Live, the model kept the 120 it invented from '2 km' even after '30 minutos'."""
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=120),
        dict(WALKING_BASE, party_walking_budget_minutes=120),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    _, answered = _walking_turns(node, "30 minutos")

    assert "walking_budget: 30" in answered["pending_hard_constraint_confirmation"]
    assert answered["constraints"].party_walking_budget_minutes == 30


def test_hours_and_minutes_answer_the_walking_question() -> None:
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=120),
        dict(WALKING_BASE, party_walking_budget_minutes=60),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    _, answered = _walking_turns(node, "1h30")

    assert "walking_budget: 90" in answered["pending_hard_constraint_confirmation"]


def test_a_bare_number_is_not_a_walking_limit_when_walking_was_not_asked() -> None:
    node, _, _ = _node(
        FakeExtractor(dict(WALKING_BASE, departure_time=None), WALKING_BASE)
    )
    messages = [HumanMessage(content="Somos 2 adultos.")]
    asked = node({"thread_id": "t1", "messages": messages, "language": "es"})
    messages += [*asked["messages"], HumanMessage(content="18")]

    update = node({"thread_id": "t1", "messages": messages, "language": "es"})

    assert "departure_time" in _asked(asked)
    assert not any(
        e.startswith("walking_budget")
        for e in update["pending_hard_constraint_confirmation"]
    )


def test_a_bare_number_answers_the_departure_when_both_were_asked() -> None:
    outputs = [
        dict(WALKING_BASE, departure_time=None, party_walking_budget_minutes=120),
        dict(WALKING_BASE, departure_time=None, party_walking_budget_minutes=120),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    first, answered = _walking_turns(node, "18", first=WALKING_VAGUE)

    assert {"departure_time", "walking_budget_unverified"} <= set(_asked(first))
    assert _asked(answered) == ["walking_budget_unverified"]


def test_a_walking_limit_given_as_a_distance_is_asked_again_in_minutes() -> None:
    """Live, the model turned '2 km' into 120 minutes; the generic question never said why."""
    node, _, _ = _node(
        FakeExtractor(dict(WALKING_BASE, party_walking_budget_minutes=120))
    )

    [first] = _walking_turns(node)

    assert _asked(first) == ["walking_budget_in_minutes"]
    assert "minutos, no en distancia" in first["messages"][0].content


def test_a_second_distance_is_converted_at_the_routing_pace_and_shown() -> None:
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=120),
        dict(WALKING_BASE, party_walking_budget_minutes=120),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    first, converted = _walking_turns(node, "unos 2 kilómetros")

    assert _asked(first) == ["walking_budget_in_minutes"]
    pending = converted["pending_hard_constraint_confirmation"]
    assert "walking_budget: 26" in pending and "walking_distance: 2 km" in pending
    assert converted["constraints"].party_walking_budget_minutes == 26
    assert "2 km" in echo_for("walking_distance: 2 km", "es")
    assert "4,5 km/h" in echo_for("walking_distance: 2 km", "es")


def test_minutes_after_the_distance_question_need_no_conversion() -> None:
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=120),
        dict(WALKING_BASE, party_walking_budget_minutes=120),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))

    _, answered = _walking_turns(node, "45")

    pending = answered["pending_hard_constraint_confirmation"]
    assert "walking_budget: 45" in pending
    assert not any(e.startswith("walking_distance") for e in pending)


def test_a_distance_answer_is_asked_in_minutes_even_when_the_model_dropped_it() -> None:
    """Offline, a model that returns no budget for '2 kilómetros' lost the guests' limit."""
    outputs = [
        dict(WALKING_BASE, party_walking_budget_minutes=50),
        dict(WALKING_BASE, party_walking_budget_minutes=None),
    ]
    node, _, _ = _node(FakeExtractor(*outputs))
    messages = [
        HumanMessage(content="Somos 2 adultos, salimos a las 18:00, caminamos poco.")
    ]
    first = node({"thread_id": "t1", "messages": messages, "language": "es"})
    messages += [*first["messages"], HumanMessage(content="2 kilómetros")]

    second = node({"thread_id": "t1", "messages": messages, "language": "es"})

    assert _asked(first) == ["walking_budget_unverified"]
    assert _asked(second) == ["walking_budget_in_minutes"]
