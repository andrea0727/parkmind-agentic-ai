"""``elicit`` node: extraction recovery, missing information, contracts."""

from datetime import date, datetime

import pytest
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from parkmind.agents.elicit_agent import (
    ExtractorOutputError,
    build_guest_profiles,
    build_party_constraints,
    echo_for,
    extract_with_recovery,
    make_elicit_node,
    missing_information,
    pending_confirmations,
)
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.core.contracts import (
    PARK_TZ,
    PreferenceSource,
    SensitivityKind,
    SensitivityLevel,
)
from parkmind.services.use_cases.resolve_attraction_names import CatalogUnavailableError

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
    node = make_elicit_node(extractor, intake, make_names(), today=lambda: TODAY, now=lambda: NOW)
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
    bad = {"guests": [{"role": "adult"}], "accessibility": [{"guest_ref": 1, "label": "my secret"}]}
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
    constraints = build_party_constraints(extraction, today=TODAY, resolution=resolution)

    assert constraints.party_size == 4
    assert [g.guest_id for g in constraints.guests] == ["g1", "g2", "g3", "g4"]
    assert constraints.must_do == ["id-tron", "id-space"]
    assert constraints.lunch_window is not None
    assert (constraints.lunch_window.start.hour, constraints.lunch_window.start.minute) == (12, 30)
    assert (constraints.lunch_window.end.hour, constraints.lunch_window.end.minute) == (13, 30)
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
    profiles = build_guest_profiles(_extraction(scenario("casual_rest_breaks_vs_queue_preference")), now=NOW)

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


def test_every_hard_item_is_pending() -> None:
    extraction = _extraction(COMPLETE)
    resolution = make_names().resolve([*extraction.must_do, *extraction.avoid])
    assert pending_confirmations(extraction, resolution) == COMPLETE["expect_pending"]


def test_a_new_extraction_replaces_stale_staging() -> None:
    node, intake, _ = _node(FakeExtractor(COMPLETE["extraction"], scenario("pure_preferences_no_accessibility")["extraction"]))

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
    node, _, _ = _node(FakeExtractor(_with(
        COMPLETE["extraction"], must_do=["tron"], avoid=["Splash"]
    )))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"].must_do == ["id-tron"]
    assert update["constraints"].avoid == ["id-splash"]
    pending = update["pending_hard_constraint_confirmation"]
    assert "must_do: TRON" in pending and "avoid: Splash Mountain" in pending


def test_two_spoken_names_for_one_attraction_become_one_id() -> None:
    node, _, _ = _node(FakeExtractor(_with(
        COMPLETE["extraction"], must_do=["TRON", "tron"]
    )))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"].must_do == ["id-tron"]


def test_unknown_attraction_is_asked_not_stored() -> None:
    node, intake, _ = _node(FakeExtractor(_with(
        COMPLETE["extraction"], avoid=["Death Star"]
    )))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["unknown_attraction:Death Star"]
    assert "Death Star" in message.content
    assert intake.pending_guest_ids("t1") == []


def test_ambiguous_attraction_lists_the_options() -> None:
    node, _, _ = _node(FakeExtractor(_with(
        COMPLETE["extraction"], must_do=["Mountain"]
    )))

    update = node(_state(*COMPLETE["messages"]))

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["ambiguous_attraction:Mountain"]
    for official in ("Space Mountain", "Splash Mountain", "Big Thunder Mountain"):
        assert official in message.content


def test_unresolved_names_are_asked_together_with_other_missing_information() -> None:
    node, _, _ = _node(FakeExtractor(_with(
        MISSING_DEPARTURE["extraction"], avoid=["Death Star"]
    )))

    update = node(_state(*MISSING_DEPARTURE["messages"]))

    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "departure_time",
        "unknown_attraction:Death Star",
    ]


def test_an_empty_catalog_is_an_error_not_a_question_to_the_guests() -> None:
    intake, _ = make_intake()
    node = make_elicit_node(
        FakeExtractor(COMPLETE["extraction"]), intake, make_names([]), today=lambda: TODAY, now=lambda: NOW
    )

    with pytest.raises(CatalogUnavailableError):
        node(_state(*COMPLETE["messages"]))
