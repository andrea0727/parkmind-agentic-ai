"""The single tool registry (Architecture section 27 [C22, C23], section 8.1 [C11]).

One list of typed tools, published two ways: by ``parkmind-mcp`` (``mcp_server``)
for external clients, and -- from P1-22 -- as the agentic context loader's
allowlist. The registry knows nothing about MCP: a tool is a name, a request
model, a data model and a handler that delegates to a use case.

Every call goes through ``ToolRegistry.call``: the arguments are validated
against the request model, the handler runs, and any exception becomes a
structured ``ToolFailure`` (``errors.to_tool_failure``).

``read_only`` means the tool has no effect on guest, plan or proposal state.
Capturing a park snapshot is data capture, not state, so a data tool that
triggers one is still read-only. The loader allowlist is the read-only tools of
the ``data`` and ``knowledge`` namespaces only (section 8.1, bound 1): the
planner is never callable from LOAD CONTEXT. No tool may activate or mutate a
plan (section 27 [C23]).
"""

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from parkmind.core.contracts import PARK_TZ
from parkmind.services.use_cases.planning_deps import DepsFactory, default_planning_deps
from parkmind.tools.contracts import ToolErrorCode, ToolResult
from parkmind.tools.errors import ToolFailure, invalid_arguments, to_tool_failure

Namespace = Literal["data", "knowledge", "planner"]
NAMESPACES: tuple[Namespace, ...] = ("data", "knowledge", "planner")
LOADER_NAMESPACES: frozenset[Namespace] = frozenset({"data", "knowledge"})

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def park_now() -> datetime:
    """Timezone-aware 'now' in park time; use cases only ever receive it injected."""
    return datetime.now(PARK_TZ)


@dataclass(frozen=True)
class ToolContext:
    """What tool handlers need to build their use cases."""

    deps_factory: DepsFactory = default_planning_deps
    clock: Callable[[], datetime] = park_now


@dataclass(frozen=True)
class ToolSpec:
    """One published tool: ``<namespace>.<name>``, typed in and out."""

    namespace: Namespace
    name: str
    description: str
    request_model: type[BaseModel]
    data_model: type[BaseModel]
    handler: Callable[[Any], ToolResult[Any]] = field(repr=False)
    read_only: bool = True

    def __post_init__(self) -> None:
        if self.namespace not in NAMESPACES:
            raise ValueError(f"unknown namespace {self.namespace!r}")
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError(
                f"tool name {self.name!r} must be snake_case, at most 64 characters"
            )
        if not self.description.strip():
            raise ValueError(f"tool {self.qualified_name} needs a description")

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def result_model(self) -> type[ToolResult[Any]]:
        """The envelope type this tool returns: ``ToolResult[data_model]``."""
        return ToolResult[self.data_model]  # type: ignore[name-defined]


ToolGroup = Callable[[ToolContext], Sequence[ToolSpec]]
"""A namespace's tools, built for one context (each ``*_tools`` module exposes one)."""


class ToolRegistry:
    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        by_name: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.qualified_name in by_name:
                raise ValueError(f"tool {spec.qualified_name} is registered twice")
            by_name[spec.qualified_name] = spec
        self._specs = by_name

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return list(self._specs)

    def get(self, qualified_name: str) -> ToolSpec:
        try:
            return self._specs[qualified_name]
        except KeyError:
            raise ToolFailure.of(
                ToolErrorCode.NOT_FOUND,
                f"No tool named {qualified_name!r}.",
                details={"available": sorted(self._specs)},
            ) from None

    def loader_allowlist(self) -> list[ToolSpec]:
        """The tools LOAD CONTEXT may call: read-only ``data.*`` and ``knowledge.*`` (section 8.1)."""
        return [s for s in self if s.read_only and s.namespace in LOADER_NAMESPACES]

    def call(
        self, qualified_name: str, arguments: Mapping[str, Any]
    ) -> ToolResult[Any]:
        """Validate, run, and return the envelope; raise ``ToolFailure`` on any failure."""
        spec = self.get(qualified_name)
        try:
            request = spec.request_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise invalid_arguments(exc) from None
        try:
            result = spec.handler(request)
        except Exception as exc:
            raise to_tool_failure(qualified_name, exc) from exc
        if not isinstance(result, ToolResult) or not isinstance(
            result.data, spec.data_model
        ):
            raise to_tool_failure(
                qualified_name,
                TypeError(
                    f"{qualified_name} returned {type(result).__name__}, not {spec.result_model.__name__}"
                ),
            )
        return result


# Each namespace registers its group here as it lands (P0-25 data, P0-26
# knowledge, P0-27 planner).
DEFAULT_GROUPS: tuple[ToolGroup, ...] = ()


def build_registry(
    context: ToolContext | None = None, groups: Sequence[ToolGroup] = DEFAULT_GROUPS
) -> ToolRegistry:
    ctx = context or ToolContext()
    return ToolRegistry([spec for group in groups for spec in group(ctx)])
