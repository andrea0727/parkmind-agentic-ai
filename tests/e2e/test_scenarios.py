"""Layer 1: every live scenario runs on real ports (Anthropic, ThemeParks, Open-Meteo, Postgres).

Opt in with ``pytest --live`` or ``PARKMIND_RUN_LIVE=1``; credentials come from the local .env,
which is loaded into the process only here and never printed.
"""

import pytest

from e2e import catalog
from e2e.kit.driver import run_scenario
from e2e.kit.live import LiveRuntime
from e2e.model import Scenario

LIVE = catalog.ordered([s.id for s in catalog.SCENARIOS if s.live])


@pytest.mark.live
@pytest.mark.parametrize("scenario", LIVE, ids=lambda s: s.id)
def test_the_journey_behaves_as_the_catalog_expects(scenario: Scenario) -> None:
    facts = run_scenario(scenario, LiveRuntime())

    status, message = scenario.judge(facts)

    assert status == "passed", f"{message}\nwhy: {scenario.why}"
