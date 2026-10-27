"""The catalog is the contract of every layer: its shape, its numbering and the generated matrix."""

import re
from pathlib import Path

import pytest

from e2e import catalog
from e2e.cli import MATRIX_PATH
from e2e.kit.matrix import render_matrix
from e2e.model import LAYERS, SEVERITIES

TESTS = Path(__file__).resolve().parents[1]


def test_ids_and_numbers_are_unique_and_numbers_are_contiguous() -> None:
    ids = [s.id for s in catalog.SCENARIOS]
    numbers = [s.number for s in catalog.SCENARIOS]

    assert len(set(ids)) == len(ids)
    assert numbers == list(range(1, len(numbers) + 1))


def test_ids_are_lowercase_words() -> None:
    assert all(re.fullmatch(r"[a-z0-9_]+", s.id) for s in catalog.SCENARIOS)


@pytest.mark.parametrize("scenario", catalog.SCENARIOS, ids=lambda s: s.id)
def test_every_scenario_is_documented(scenario) -> None:
    assert scenario.severity in SEVERITIES
    assert scenario.title and scenario.what and scenario.expected and scenario.setup
    assert scenario.steps
    assert scenario.texts()


@pytest.mark.parametrize("scenario", catalog.SCENARIOS, ids=lambda s: s.id)
def test_every_scenario_says_what_it_proves_at_the_other_layers(scenario) -> None:
    assert scenario.criteria.observability, "observability criterion missing"
    assert len(scenario.layers()) >= 2


def test_every_layer_is_covered_by_some_scenario() -> None:
    covered = {layer for s in catalog.SCENARIOS for layer in s.layers()}

    assert covered == set(LAYERS)


@pytest.mark.parametrize("scenario", catalog.SCENARIOS, ids=lambda s: s.id)
def test_referenced_tests_exist(scenario) -> None:
    for ref in scenario.criteria.refs:
        path, _, _ = ref.partition("::")
        assert (TESTS / path).exists(), f"{scenario.id} refers to missing tests/{path}"


def test_scenarios_that_close_an_attraction_have_a_follow_up() -> None:
    closing = [s for s in catalog.SCENARIOS if s.follow_up is not None]

    assert {s.id for s in closing} >= {
        "opcion_cerrada_alternativa",
        "opcion_cerrada_alternativa_rechazada",
    }


def test_the_scenario_matrix_is_up_to_date() -> None:
    assert MATRIX_PATH.read_text() == render_matrix(catalog.SCENARIOS), (
        "docs/testing/SCENARIO_MATRIX.md is stale; regenerate it with "
        "`PYTHONPATH=src:tests python -m e2e.cli matrix`"
    )
