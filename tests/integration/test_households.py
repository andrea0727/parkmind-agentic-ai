"""Saved parties: parsed strictly, handed to the planner once, never carrying safety facts."""

import json

import pytest
from conversation_support import FIRST_REQUEST, make_rig
from fastapi.testclient import TestClient
from planning_support import factory_for

from parkmind.api.app import create_app
from parkmind.services.personalization.household import (
    HouseholdFileError,
    parse_households,
)
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase
from server import load_households

FAMILY = {
    "party": [
        {"role": "adult", "label": "tu pareja", "preferences": "likes shows"},
        {"role": "child", "label": "tu hija"},
    ]
}


def test_a_household_reads_as_one_message_for_the_planner():
    household = parse_households({"fam": FAMILY})["fam"]

    message = household.as_message()

    assert message.startswith("SAVED PARTY")
    assert '1. adult, called "tu pareja", preferences: likes shows' in message
    assert '2. child, called "tu hija"' in message


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"u": {}},
        {"u": {"party": []}},
        {"u": {"party": [{"role": "baby"}]}},
        {"u": {"party": [{"role": "child", "height_cm": 100}]}},
        {"u": {"party": [{"role": "adult", "preferences": 3}]}},
    ],
)
def test_a_malformed_household_is_refused(raw):
    with pytest.raises(HouseholdFileError):
        parse_households(raw)


def test_a_missing_file_means_no_saved_parties(tmp_path):
    assert load_households(tmp_path / "nope.json") == {}


def test_a_broken_file_is_an_error(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{not json")

    with pytest.raises(HouseholdFileError):
        load_households(path)


def _client(rig, households):
    app = create_app(
        rig.service,
        DescribePlanUseCase(factory_for(rig.deps)),
        env={},
        argv=[],
        saved_parties={n: h.as_message() for n, h in households.items()},
    )
    return TestClient(app)


def test_the_saved_party_reaches_the_extractor_once_before_the_first_message(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"anonymous": FAMILY}))
    rig = make_rig()
    with _client(rig, load_households(path)) as client:
        t = {"thread_id": client.post("/api/session").json()["thread_id"]}
        client.post("/api/message", params=t, json={"text": FIRST_REQUEST})

    texts, _ = rig.extractor.calls[0]
    assert texts[0].startswith("SAVED PARTY") and texts[-1] == FIRST_REQUEST


def test_an_account_without_a_saved_party_is_unchanged():
    rig = make_rig()
    with _client(rig, {}) as client:
        t = {"thread_id": client.post("/api/session").json()["thread_id"]}
        client.post("/api/message", params=t, json={"text": FIRST_REQUEST})

    assert rig.extractor.calls[0][0] == [FIRST_REQUEST]
