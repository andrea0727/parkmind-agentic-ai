"""The scenario catalog: every journey the project proves, grouped by area.

Numbers 1-33 are the original E2E scenarios and stay stable so history and documents keep
pointing at the same journey; new scenarios take the next number.
"""

from e2e.catalog import (
    approval,
    constraints,
    elicitation,
    happy_path,
    replanning,
    resilience,
    safety,
    security,
)
from e2e.model import Scenario

SCENARIOS: tuple[Scenario, ...] = tuple(
    sorted(
        (
            s
            for module in (
                happy_path,
                safety,
                approval,
                elicitation,
                constraints,
                resilience,
                replanning,
                security,
            )
            for s in module.SCENARIOS
        ),
        key=lambda s: s.number,
    )
)

BY_ID: dict[str, Scenario] = {s.id: s for s in SCENARIOS}

# The empty-database outage has to run before the empty-database happy path, which fills that catalog.
_FIRST = ("proveedor_caido_db_vacia", "happy_catalogo_vacio")
ORDER: tuple[str, ...] = _FIRST + tuple(s.id for s in SCENARIOS if s.id not in _FIRST)


def by_id(scenario_id: str) -> Scenario:
    return BY_ID[scenario_id]


def ordered(ids: list[str] | None = None) -> list[Scenario]:
    wanted = set(ids) if ids else None
    return [BY_ID[i] for i in ORDER if wanted is None or i in wanted]
