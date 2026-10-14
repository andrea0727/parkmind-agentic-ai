"""Records which planning stages ran and what the extractor returned."""

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Protocol, Self

from parkmind.graph import initial_planning_graph as ipg

_STAGES = (
    (ipg.LoadContextUseCase, "execute", "load_context"),
    (ipg.BuildPlanUseCase, "resolve_group", "resolve_group"),
    (ipg.BuildPlanUseCase, "build", "build_plan"),
    (ipg.CheckPlanUseCase, "execute", "check"),
    (ipg.ExplainPlanUseCase, "prepare", "explain"),
    (ipg.ProposePlanUseCase, "execute", "propose"),
    (ipg.ResolveProposalUseCase, "execute", "resolve_proposal"),
)


class Extractor(Protocol):
    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]: ...


class StageSpy:
    """While active, every planning use case appends its stage label to ``stages``."""

    def __init__(self) -> None:
        self.stages: list[str] = []
        self._saved: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> Self:
        for cls, method, label in _STAGES:
            original = getattr(cls, method)
            self._saved.append((cls, method, original))
            setattr(cls, method, self._wrap(original, label))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for cls, method, original in self._saved:
            setattr(cls, method, original)
        self._saved.clear()

    def _wrap(self, original: Any, label: str) -> Any:
        def spy(this: Any, *args: Any, **kwargs: Any) -> Any:
            self.stages.append(label)
            return original(this, *args, **kwargs)

        return spy

    def take(self) -> list[str]:
        taken, self.stages = list(self.stages), []
        return taken


class RecordingExtractor:
    """Wraps any extractor and keeps each call's arguments for the report."""

    def __init__(self, inner: Extractor) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        args = self._inner.extract(human_messages, repair_hint)
        self.calls.append({"hint": repair_hint, "args": args})
        return args
