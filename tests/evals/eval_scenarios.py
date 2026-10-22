"""Shared inputs for the evals: a two-guest plan with a height-restricted ride, and its explanation."""

import json
from pathlib import Path

import pytest

pytest.importorskip("deepeval")

from datetime import timedelta

import factories
from elicit_support import catalog

from evals.eval_metrics import ConstraintSnapshot
from parkmind.core.contracts import CheckResult, Guest, Plan
from parkmind.services.use_cases.explain_plan import (
    ExplanationPayload,
    explanation_payload,
)

EXPECTATIONS_PATH = Path(__file__).parent / "fixtures" / "constraint_expectations.json"
NAMES = {"a1": "Space Mountain", "a2": "Big Thunder Mountain"}
HEIGHT_LIMITS = {"a1": 112}

GOOD_EXPLANATION = (
    "Your plan has 2 stops. You start at Space Mountain at 10:00, with a 20 minute wait. "
    "Guest 2 sits this one out because it needs 112 cm. "
    "Then you go to Big Thunder Mountain at 10:40, with a 15 minute wait. "
    "In total you wait 35 minutes and walk 10 minutes."
)

GOOD_EXPLANATION_ES = (
    "Tu plan tiene 2 paradas. Empiezas en Space Mountain a las 10:00, con 20 minutos de espera. "
    "Guest 2 no sube a esta atracción porque necesita 112 cm. "
    "Luego vas a Big Thunder Mountain a las 10:40, con 15 minutos de espera. "
    "En total esperas 35 minutos y caminas 10 minutos."
)

REQUEST = (
    "We are two adults and our daughter, she is 100 cm. We want to ride Space Mountain."
)
REQUEST_ES = "Somos dos adultos y nuestra hija, que mide 100 cm. Queremos subir a Space Mountain."


def guests() -> list[Guest]:
    return [
        factories.guest(guest_id="g1", role="adult", height_cm=175),
        factories.guest(guest_id="g2", role="child", height_cm=100),
    ]


def day_plan() -> Plan:
    first = factories.stop(node_id="a1", served_guests=["g1"])
    second = factories.stop(
        node_id="a2",
        arrival_time=first.departure_time + timedelta(minutes=10),
        departure_time=first.departure_time + timedelta(minutes=40),
        expected_wait_minutes=15.0,
        walking_minutes=5.0,
        served_guests=["g1", "g2"],
    )
    return factories.plan(
        stops=[first, second], total_wait_minutes=35.0, total_walking_minutes=10.0
    )


def payload() -> ExplanationPayload:
    return explanation_payload(
        day_plan(),
        CheckResult(valid=True),
        NAMES,
        guests=guests(),
        height_limits=HEIGHT_LIMITS,
    )


def known_attractions() -> list[str]:
    return sorted({*NAMES.values(), *(a.name for a in catalog())})


def load_expectations() -> dict[str, ConstraintSnapshot]:
    raw = json.loads(EXPECTATIONS_PATH.read_text())["expectations"]
    return {
        scenario_id: ConstraintSnapshot.model_validate(snapshot)
        for scenario_id, snapshot in raw.items()
    }
