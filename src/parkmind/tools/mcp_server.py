"""``parkmind-mcp``: the tool registry served over MCP (P0-24).

One server, three namespaces (``data.*``, ``knowledge.*``, ``planner.*``;
Architecture section 28). MCP is a transport, not the critical path (section
27 [C22]): the graph calls the same use cases in-process, so nothing here holds
business logic. This is the only module of the package that imports the MCP
SDK.

Each ``ToolSpec`` becomes one MCP tool:

* its arguments are the request model's fields, flat (no wrapper object), so
  the published ``inputSchema`` is the request model's own schema;
* its ``outputSchema`` is ``ToolResult[data_model]``, the section 32 envelope;
* a ``ToolFailure`` is returned as ``is_error=True`` with the payload as
  ``structuredContent`` (``{"error": {...}}``) and as JSON text;
* ``readOnlyHint`` mirrors ``ToolSpec.read_only``; nothing is destructive.

Arguments of the wrong JSON type are rejected by the SDK before the registry
sees them (``is_error=True``, SDK wording). That message repeats the offending
value to the caller who sent it; tool inputs carry ids and constraints, never
accessibility requirements (planner tools take a session reference, C19).
"""

import inspect
import json
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from parkmind.tools.errors import ToolFailure
from parkmind.tools.registry import ToolRegistry, ToolSpec, build_registry

SERVER_NAME = "parkmind-mcp"
INSTRUCTIONS = (
    "ParkMind's capability boundary for Magic Kingdom: data.* reads park data "
    "from the latest snapshot, knowledge.* searches the reviewed knowledge corpus "
    "and checks accessibility, planner.* builds and checks candidate plans. "
    "Every result is {data, provenance}. No tool activates or changes a plan."
)


def _error_result(failure: ToolFailure) -> CallToolResult:
    body = {"error": failure.payload.model_dump(mode="json")}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body))],
        structured_content=body,
        is_error=True,
    )


def _tool_function(registry: ToolRegistry, spec: ToolSpec) -> Any:
    """A function the SDK can introspect: flat keyword parameters, envelope return type."""

    def call(**arguments: Any) -> Any:
        try:
            return registry.call(spec.qualified_name, arguments)
        except ToolFailure as failure:
            return _error_result(failure)

    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=info.annotation,
            default=inspect.Parameter.empty
            if info.is_required()
            else info.get_default(),
        )
        for name, info in spec.request_model.model_fields.items()
    ]
    call.__name__ = spec.qualified_name.replace(".", "_")
    call.__doc__ = spec.description
    signature = inspect.Signature(parameters, return_annotation=spec.result_model)
    call.__signature__ = signature  # type: ignore[attr-defined]
    call.__annotations__ = {
        **{p.name: p.annotation for p in parameters},
        "return": spec.result_model,
    }
    return call


def create_server(registry: ToolRegistry | None = None) -> MCPServer:
    """An MCP server publishing every tool of ``registry`` (the default one if omitted)."""
    tools = registry if registry is not None else build_registry()
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    for spec in tools:
        server.add_tool(
            _tool_function(tools, spec),
            name=spec.qualified_name,
            title=spec.qualified_name,
            description=spec.description,
            annotations=ToolAnnotations(
                read_only_hint=spec.read_only,
                destructive_hint=False,
                idempotent_hint=spec.read_only,
            ),
            structured_output=True,
        )
    return server
