"""Exceptions become structured tool errors (P0-24 Done-when: "Errors are structured").

No message or detail may echo a caller's argument values or a driver's text.
"""

import pytest
from pydantic import BaseModel

from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    PlanningUnavailableError,
)
from parkmind.tools.contracts import ToolErrorCode, ToolProvenance, ToolResult
from parkmind.tools.errors import ToolFailure, to_tool_failure
from parkmind.tools.registry import ToolRegistry, ToolSpec

SECRET = "SECRET-VALUE-WHEELCHAIR"


class Request(BaseModel):
    attraction_ids: list[str]
    limit: int


class Data(BaseModel):
    ok: bool


def _registry(handler) -> ToolRegistry:  # type: ignore[no-untyped-def]
    return ToolRegistry(
        [
            ToolSpec(
                namespace="data",
                name="probe",
                description="Probe.",
                request_model=Request,
                data_model=Data,
                handler=handler,
            )
        ]
    )


def _ok(request: Request) -> ToolResult[Data]:
    return ToolResult[Data](
        data=Data(ok=True), provenance=ToolProvenance(source="test")
    )


def _failure(handler, arguments: dict) -> ToolFailure:  # type: ignore[no-untyped-def,type-arg]
    with pytest.raises(ToolFailure) as excinfo:
        _registry(handler).call("data.probe", arguments)
    return excinfo.value


def test_invalid_arguments_name_the_fields_never_the_values() -> None:
    failure = _failure(_ok, {"attraction_ids": SECRET, "limit": SECRET})

    assert failure.payload.code is ToolErrorCode.INVALID_ARGUMENT
    assert failure.payload.details == {"fields": ["attraction_ids", "limit"]}
    assert SECRET not in failure.payload.model_dump_json()


def test_missing_park_data_is_unavailable_and_retryable() -> None:
    def handler(request: Request) -> ToolResult[Data]:
        raise ContextUnavailableError("no live data and no valid stored snapshot")

    failure = _failure(handler, {"attraction_ids": [], "limit": 1})

    assert failure.payload.code is ToolErrorCode.UNAVAILABLE
    assert failure.payload.retryable is True
    assert failure.payload.message == "no live data and no valid stored snapshot"


def test_a_failing_port_is_unavailable_without_the_driver_text() -> None:
    driver_text = (
        'OperationalError: connection to server at "10.0.0.7", port 5432 failed'
    )

    def handler(request: Request) -> ToolResult[Data]:
        raise PlanningUnavailableError(driver_text)

    failure = _failure(handler, {"attraction_ids": [], "limit": 1})

    assert failure.payload.code is ToolErrorCode.UNAVAILABLE
    assert failure.payload.retryable is True
    assert "10.0.0.7" not in failure.payload.model_dump_json()


def test_an_unexpected_exception_is_internal_and_hides_its_message() -> None:
    def handler(request: Request) -> ToolResult[Data]:
        raise RuntimeError(f"secret internals {SECRET}")

    failure = _failure(handler, {"attraction_ids": [], "limit": 1})

    assert failure.payload.code is ToolErrorCode.INTERNAL
    assert failure.payload.retryable is False
    assert SECRET not in failure.payload.model_dump_json()


def test_a_tool_failure_raised_by_a_handler_passes_through_unchanged() -> None:
    raised = ToolFailure.of(
        ToolErrorCode.NOT_FOUND, "No attraction 'x'.", details={"ids": ["x"]}
    )

    def handler(request: Request) -> ToolResult[Data]:
        raise raised

    assert _failure(handler, {"attraction_ids": ["x"], "limit": 1}) is raised


def test_to_tool_failure_maps_the_subclass_before_the_base_class() -> None:
    specific = to_tool_failure("data.probe", ContextUnavailableError("no schedule"))
    generic = to_tool_failure("data.probe", PlanningUnavailableError("raw"))

    assert specific.payload.message == "no schedule"
    assert generic.payload.message != "raw"
