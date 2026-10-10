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

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from parkmind.tools.contracts import ToolErrorCode, ToolResult
from parkmind.tools.data_tools import data_tools
from parkmind.tools.errors import ToolFailure, invalid_arguments, to_tool_failure
from parkmind.tools.knowledge_tools import knowledge_tools
from parkmind.tools.spec import (
    LOADER_NAMESPACES,
    NAMESPACES,
    Namespace,
    ToolContext,
    ToolGroup,
    ToolSpec,
    park_now,
)

__all__ = [
    "DEFAULT_GROUPS",
    "LOADER_NAMESPACES",
    "NAMESPACES",
    "Namespace",
    "ToolContext",
    "ToolGroup",
    "ToolRegistry",
    "ToolSpec",
    "build_registry",
    "park_now",
]


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
DEFAULT_GROUPS: tuple[ToolGroup, ...] = (data_tools, knowledge_tools)


def build_registry(
    context: ToolContext | None = None, groups: Sequence[ToolGroup] = DEFAULT_GROUPS
) -> ToolRegistry:
    ctx = context or ToolContext()
    return ToolRegistry([spec for group in groups for spec in group(ctx)])
