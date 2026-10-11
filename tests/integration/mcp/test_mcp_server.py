"""parkmind-mcp over the real MCP protocol (P0-24).

Most tests connect an MCP ``Client`` to the server in-process; one launches
``scripts/run_mcp_server.py`` as a subprocess over stdio, exactly as an external
client (the LLM-only baseline harness, P1-15) would. No MCP server needs to be
running beforehand.
"""

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import BaseModel, Field

from parkmind.services.use_cases.planning_deps import ContextUnavailableError
from parkmind.tools.contracts import ToolProvenance, ToolResult
from parkmind.tools.mcp_server import SERVER_NAME, create_server
from parkmind.tools.registry import ToolRegistry, ToolSpec, build_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_SCRIPT = REPO_ROOT / "scripts" / "run_mcp_server.py"


class WaitsRequest(BaseModel):
    attraction_ids: list[str]
    limit: int = 5


class WaitsData(BaseModel):
    waits: dict[str, float]


def _waits(request: WaitsRequest) -> ToolResult[WaitsData]:
    return ToolResult[WaitsData](
        data=WaitsData(
            waits={a: 15.0 for a in request.attraction_ids[: request.limit]}
        ),
        provenance=ToolProvenance(
            source="ThemeParks.wiki", snapshot_id="snap_1", age_seconds=60.0
        ),
    )


def _unavailable(request: WaitsRequest) -> ToolResult[WaitsData]:
    raise ContextUnavailableError("no live data and no valid stored snapshot")


def _crash(request: WaitsRequest) -> ToolResult[WaitsData]:
    raise RuntimeError("secret internals")


def _tool(
    name: str, handler: Any, namespace: Any = "data", read_only: bool = True
) -> ToolSpec:
    return ToolSpec(
        namespace=namespace,
        name=name,
        description=f"{name} for tests.",
        request_model=WaitsRequest,
        data_model=WaitsData,
        handler=handler,
        read_only=read_only,
    )


REGISTRY = ToolRegistry(
    [
        _tool("get_live_waits", _waits),
        _tool("unavailable", _unavailable),
        _tool("crash", _crash),
        _tool("build_plan", _waits, namespace="planner"),
    ]
)


def _session(coro_fn: Any) -> Any:
    async def run() -> Any:
        async with Client(create_server(REGISTRY)) as client:
            return await coro_fn(client)

    return asyncio.run(run())


def test_in_memory_session_lists_tools() -> None:
    tools = _session(lambda c: c.list_tools()).tools
    by_name = {t.name: t for t in tools}

    assert set(by_name) == {
        "data.get_live_waits",
        "data.unavailable",
        "data.crash",
        "planner.build_plan",
    }
    waits = by_name["data.get_live_waits"]
    assert set(waits.input_schema["properties"]) == {
        "attraction_ids",
        "limit",
    }  # flat, no wrapper
    assert waits.input_schema["required"] == ["attraction_ids"]
    assert set(waits.output_schema["properties"]) == {"data", "provenance"}
    assert waits.annotations.read_only_hint is True
    assert waits.annotations.destructive_hint is False


def test_a_successful_call_returns_the_section_32_envelope() -> None:
    result = _session(
        lambda c: c.call_tool(
            "data.get_live_waits", {"attraction_ids": ["tron", "space"], "limit": 1}
        )
    )

    assert result.is_error is False
    assert result.structured_content["data"] == {"waits": {"tron": 15.0}}
    provenance = result.structured_content["provenance"]
    assert provenance["source"] == "ThemeParks.wiki"
    assert provenance["snapshot_id"] == "snap_1"


def test_error_result_is_structured() -> None:
    result = _session(lambda c: c.call_tool("data.unavailable", {"attraction_ids": []}))

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "UNAVAILABLE"
    assert error["retryable"] is True
    assert error["message"] == "no live data and no valid stored snapshot"


def test_an_unexpected_failure_hides_its_internals() -> None:
    result = _session(lambda c: c.call_tool("data.crash", {"attraction_ids": []}))

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "INTERNAL"
    assert "secret internals" not in result.content[0].text


def test_arguments_of_the_wrong_type_are_rejected_before_the_tool_runs() -> None:
    calls: list[WaitsRequest] = []

    def spy(request: WaitsRequest) -> ToolResult[WaitsData]:
        calls.append(request)
        return _waits(request)

    registry = ToolRegistry([_tool("get_live_waits", spy)])

    async def run() -> Any:
        async with Client(create_server(registry)) as client:
            return await client.call_tool(
                "data.get_live_waits", {"attraction_ids": "tron"}
            )

    result = asyncio.run(run())

    assert result.is_error is True
    assert calls == []


def test_stdio_client_lists_all_namespaced_tools() -> None:
    """The server script starts locally and answers an external MCP client over stdio."""
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_SCRIPT)], cwd=str(REPO_ROOT)
    )

    async def run() -> tuple[Any, list[str]]:
        async with Client(params) as client:
            tools = await client.list_tools()
            return client.server_info, [t.name for t in tools.tools]

    server_info, names = asyncio.run(asyncio.wait_for(run(), timeout=60))

    assert server_info is not None and server_info.name == SERVER_NAME
    assert sorted(names) == sorted(build_registry().names())


class _TagsRequest(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


def test_fields_with_a_default_factory_are_optional_over_mcp() -> None:
    """Regression: a ``default_factory`` field was published as required."""

    def handler(request: _TagsRequest) -> ToolResult[WaitsData]:
        return ToolResult[WaitsData](
            data=WaitsData(waits={t: 0.0 for t in request.tags}),
            provenance=ToolProvenance(source="test"),
        )

    registry = ToolRegistry(
        [
            ToolSpec(
                namespace="data",
                name="tagged",
                description="Tagged.",
                request_model=_TagsRequest,
                data_model=WaitsData,
                handler=handler,
            )
        ]
    )

    async def run() -> Any:
        async with Client(create_server(registry)) as client:
            (tool,) = (await client.list_tools()).tools
            return tool, await client.call_tool("data.tagged", {"name": "x"})

    tool, result = asyncio.run(run())

    assert tool.input_schema["required"] == ["name"]
    assert result.is_error is False
    assert result.structured_content["data"] == {"waits": {}}


_LIMITS = frozenset(
    {
        "description",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
    }
)


def _limits(schema: Any, path: str = "") -> set[tuple[str, str, str]]:
    """Every limit or description in a JSON schema, with where it sits."""
    found: set[tuple[str, str, str]] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in _LIMITS and not isinstance(value, dict):
                found.add((path, key, repr(value)))
            found |= _limits(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for i, item in enumerate(schema):
            found |= _limits(item, f"{path}[{i}]")
    return found


def test_every_tool_publishes_its_request_models_limits() -> None:
    """The published inputSchema keeps what the registry enforces (lengths, ranges, sizes)."""
    from mcp_support import Deps, list_tools

    registry = Deps(collect_at=None).registry()
    published = {t.name: t.input_schema for t in list_tools(registry)}

    for spec in registry:
        model = spec.request_model.model_json_schema()
        schema = published[spec.qualified_name]
        assert set(schema.get("required", [])) == set(model.get("required", []))
        for field in spec.request_model.model_fields:
            assert _limits(schema["properties"][field]) == _limits(
                model["properties"][field]
            ), f"{spec.qualified_name}.{field}"


def test_http_refuses_a_host_other_than_loopback() -> None:
    """No authentication, so no network: the script exits before binding anything."""
    run = subprocess.run(
        [sys.executable, str(SERVER_SCRIPT), "--http", "--host", "0.0.0.0"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert run.returncode == 2
    assert "not a loopback address" in run.stderr
