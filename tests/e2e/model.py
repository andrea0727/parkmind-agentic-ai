"""The scenario model: what a journey is, what it must prove and at which layers."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Facts = dict[str, Any]
Verdict = tuple[str, str]
Judge = Callable[[Facts], Verdict]
Messages = tuple[str, ...] | Callable[[], tuple[str, ...]]

CONFIRM: Mapping[str, Any] = {"confirmed": True, "consent": True}
APPROVE: Mapping[str, Any] = {"decision": "APPROVED"}

LAYERS = ("e2e", "workflow", "llm_eval", "observability", "business_rules")
SEVERITIES = ("blocker", "critical", "normal", "minor")


class Fault(str, Enum):
    """An infrastructure failure injected around a live run."""

    PROVIDER_DOWN = "provider_down"
    EMPTY_CATALOG_DB = "empty_catalog_db"
    PROVIDER_DOWN_EMPTY_DB = "provider_down_empty_db"
    DB_DOWN = "db_down"


@dataclass(frozen=True)
class Criteria:
    """What the scenario must show at each layer; an empty string means the layer is not covered.

    The E2E criterion is the scenario's own ``expected``. ``refs`` lists the tests of the
    other layers that carry the criteria, as paths relative to ``tests/``.
    """

    workflow: str = ""
    llm_eval: str = ""
    observability: str = ""
    business_rules: str = ""
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Offline:
    """How the workflow layer replays a scenario without network, database or LLM."""

    extraction: Mapping[str, Any]
    judge: Judge | None = None
    down: tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowUp:
    """After the plan is approved, an event arrives and the replanning graph runs."""

    kind: str
    stop_index: int = 0
    decision: Mapping[str, Any] = field(default_factory=lambda: dict(APPROVE))


@dataclass(frozen=True)
class Scenario:
    id: str
    number: int
    area: str
    severity: str
    title: str
    what: str
    steps: tuple[str, ...]
    setup: str
    messages: Messages
    expected: str
    judge: Judge
    criteria: Criteria
    why: str = ""
    confirm: Any = field(default_factory=lambda: dict(CONFIRM))
    approval: Any = field(default_factory=lambda: dict(APPROVE))
    fault: Fault | None = None
    live: bool = True
    offline: Offline | None = None
    follow_up: FollowUp | None = None

    def texts(self) -> tuple[str, ...]:
        return self.messages() if callable(self.messages) else self.messages

    def layers(self) -> tuple[str, ...]:
        covered = ["e2e"] if self.live else []
        for layer in LAYERS[1:]:
            if getattr(self.criteria, layer):
                covered.append(layer)
        if self.offline is not None and "workflow" not in covered:
            covered.append("workflow")
        return tuple(covered)
