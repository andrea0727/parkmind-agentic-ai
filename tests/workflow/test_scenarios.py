"""Layer 2: every scenario with an offline spec is replayed through the real graphs and judged.

The judge is the one the live E2E layer uses, so a behavior the journey proves on real ports is
also pinned here, deterministically and without credentials.
"""

import pytest
from e2e import catalog
from e2e.kit.driver import run_scenario
from e2e.model import Scenario
from workflow.runtime import OfflineRuntime

REPLAYED = [s for s in catalog.SCENARIOS if s.offline is not None]


@pytest.mark.parametrize("scenario", REPLAYED, ids=lambda s: s.id)
def test_the_scenario_behaves_as_the_catalog_expects(scenario: Scenario) -> None:
    assert scenario.offline is not None
    facts = run_scenario(scenario, OfflineRuntime())
    judge = scenario.offline.judge or scenario.judge

    status, message = judge(facts)

    assert status == "passed", message
