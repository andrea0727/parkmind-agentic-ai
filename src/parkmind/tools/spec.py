"""What a tool is: ``ToolSpec``, built per ``ToolContext`` by a namespace's ``ToolGroup``.

Kept apart from ``registry`` so the ``*_tools`` modules can define their specs
while the registry imports those modules for its default groups.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from parkmind.core.contracts import PARK_TZ
from parkmind.services.use_cases.knowledge_queries import (
    KnowledgeSearchFactory,
    default_knowledge_search,
)
from parkmind.services.use_cases.planning_deps import DepsFactory, default_planning_deps
from parkmind.tools.contracts import ToolResult

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
    knowledge_search: KnowledgeSearchFactory = default_knowledge_search
    """Semantic search for ``knowledge.*``: its own connection, not the planner's ports."""


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
