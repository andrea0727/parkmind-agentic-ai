"""The tool registry (P0-24): typed tools, namespaced names, one dispatch path."""

from typing import Any

import pytest
from pydantic import BaseModel

from parkmind.tools.contracts import ToolErrorCode, ToolProvenance, ToolResult
from parkmind.tools.errors import ToolFailure
from parkmind.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolSpec,
    build_registry,
)


class EchoRequest(BaseModel):
    text: str
    times: int = 1


class EchoData(BaseModel):
    echoes: list[str]


def _echo(request: EchoRequest) -> ToolResult[EchoData]:
    return ToolResult[EchoData](
        data=EchoData(echoes=[request.text] * request.times),
        provenance=ToolProvenance(source="test"),
    )


def spec(namespace: Any = "data", name: str = "echo", **overrides: Any) -> ToolSpec:
    fields: dict[str, Any] = {
        "namespace": namespace,
        "name": name,
        "description": "Repeats the text.",
        "request_model": EchoRequest,
        "data_model": EchoData,
        "handler": _echo,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_tools_are_published_under_their_namespace() -> None:
    registry = ToolRegistry([spec("data", "echo"), spec("planner", "echo")])

    assert registry.names() == ["data.echo", "planner.echo"]


def test_a_tool_name_is_registered_once() -> None:
    with pytest.raises(ValueError, match="registered twice"):
        ToolRegistry([spec(), spec()])


@pytest.mark.parametrize("name", ["GetWaits", "get-waits", "", "x" * 65, "1st"])
def test_tool_names_are_snake_case(name: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        spec(name=name)


def test_namespaces_are_a_closed_set() -> None:
    with pytest.raises(ValueError, match="namespace"):
        spec(namespace="admin")


def test_every_tool_needs_a_description() -> None:
    with pytest.raises(ValueError, match="description"):
        spec(description="  ")


def test_loader_allowlist_is_read_only_data_and_knowledge_only() -> None:
    registry = ToolRegistry(
        [
            spec("data", "waits"),
            spec("knowledge", "search"),
            spec("planner", "build"),
            spec("data", "writer", read_only=False),
        ]
    )

    assert [s.qualified_name for s in registry.loader_allowlist()] == [
        "data.waits",
        "knowledge.search",
    ]


def test_a_call_validates_the_request_and_returns_the_envelope() -> None:
    registry = ToolRegistry([spec()])

    result = registry.call("data.echo", {"text": "hi", "times": 2})

    assert result.data == EchoData(echoes=["hi", "hi"])
    assert result.provenance.source == "test"


def test_an_unknown_tool_is_not_found() -> None:
    with pytest.raises(ToolFailure) as excinfo:
        ToolRegistry([spec()]).call("data.nope", {})

    assert excinfo.value.payload.code is ToolErrorCode.NOT_FOUND
    assert excinfo.value.payload.details == {"available": ["data.echo"]}


def test_a_handler_returning_the_wrong_type_is_an_internal_error() -> None:
    registry = ToolRegistry([spec(handler=lambda request: {"echoes": []})])

    with pytest.raises(ToolFailure) as excinfo:
        registry.call("data.echo", {"text": "hi"})

    assert excinfo.value.payload.code is ToolErrorCode.INTERNAL


def test_the_result_model_is_the_section_32_envelope() -> None:
    schema = spec().result_model.model_json_schema()

    assert set(schema["properties"]) == {"data", "provenance"}
    assert set(schema["required"]) == {"data", "provenance"}


def test_build_registry_collects_every_group_for_one_context() -> None:
    seen: list[ToolContext] = []

    def group_a(ctx: ToolContext) -> list[ToolSpec]:
        seen.append(ctx)
        return [spec("data", "a")]

    def group_b(ctx: ToolContext) -> list[ToolSpec]:
        seen.append(ctx)
        return [spec("knowledge", "b")]

    context = ToolContext()
    registry = build_registry(context, groups=(group_a, group_b))

    assert registry.names() == ["data.a", "knowledge.b"]
    assert seen == [context, context]


def test_every_tool_publishes_input_and_output_schema() -> None:
    """P0-24: "Tools expose typed input/output contracts" -- every default tool."""
    registry = build_registry()

    assert len(registry) > 0
    for tool in registry:
        request_schema = tool.request_model.model_json_schema()
        result_schema = tool.result_model.model_json_schema()
        assert request_schema["type"] == "object", tool.qualified_name
        assert set(result_schema["properties"]) == {"data", "provenance"}, (
            tool.qualified_name
        )
        assert tool.description.strip(), tool.qualified_name


def test_the_default_registry_publishes_the_seven_data_tools() -> None:
    names = build_registry().names()

    assert [n for n in names if n.startswith("data.")] == [
        "data.get_live_waits",
        "data.get_attraction_status",
        "data.get_schedule",
        "data.get_showtimes",
        "data.get_weather",
        "data.get_walking_time",
        "data.get_attraction_info",
    ]
