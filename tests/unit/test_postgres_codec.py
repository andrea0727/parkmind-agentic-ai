"""The JSONB codec keeps every §33 contract intact and never leaks stored values."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import factories
import pytest
from pydantic import BaseModel

from parkmind.services.clients.postgres.codec import (
    from_payload,
    raw_to_jsonb,
    to_jsonb,
)
from parkmind.services.ports import StoredDataError

BUILDERS: list[Callable[[], BaseModel]] = [
    factories.guest,
    factories.guest_profile,
    factories.accessibility,
    factories.behavior_entry,
    factories.provenance,
    factories.plan,
    factories.proposal,
    factories.execution_state,
    factories.event,
    factories.live_context,
    factories.attraction,
    factories.park,
]


def _through_json(model: BaseModel) -> Any:
    """What a JSONB column does: dump to JSON text, load it back as Python."""
    return json.loads(json.dumps(to_jsonb(model).obj))


@pytest.mark.parametrize("build", BUILDERS, ids=lambda build: build.__name__)
def test_every_contract_round_trips_through_jsonb(build: Callable[[], BaseModel]) -> None:
    original = build()

    restored = from_payload(type(original), _through_json(original), what="test row")

    assert restored == original


def test_datetimes_stay_timezone_aware_and_denote_the_same_instant() -> None:
    original = factories.live_context()

    restored = from_payload(type(original), _through_json(original), what="snapshot")

    assert restored.retrieved_at.tzinfo is not None
    assert restored.retrieved_at == factories.NOW
    assert restored.retrieved_at.utcoffset() == timedelta(hours=-4)
    assert all(t.tzinfo is not None for t in restored.showtimes["a1"])


def test_a_drifted_payload_raises_stored_data_error() -> None:
    payload = _through_json(factories.guest_profile())
    payload["queue_tolerance"]["confidence"] = 7  # outside 0..1

    with pytest.raises(StoredDataError, match="GuestProfile"):
        from_payload(type(factories.guest_profile()), payload, what="profile g1")


def test_stored_data_error_never_leaks_accessibility_values() -> None:
    payload = _through_json(factories.accessibility())
    payload["consent"] = False  # flags without consent: invalid by contract

    with pytest.raises(StoredDataError) as excinfo:
        from_payload(type(factories.accessibility()), payload, what="accessibility g1")

    text = f"{excinfo.value!r} {excinfo.value}"
    assert "WHEELCHAIR" not in text
    assert "HIGH_G_FORCE" not in text
    # `from None`: no chained ValidationError (which prints the input) in tracebacks.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_raw_provider_payload_is_stored_verbatim() -> None:
    raw = {"liveData": [{"id": "x", "queue": {"STANDBY": {"waitTime": None}}}], "n": 1.5}

    assert json.loads(json.dumps(raw_to_jsonb(raw).obj)) == raw
