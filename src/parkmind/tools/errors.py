"""From exceptions to structured tool errors (P0-24: "Errors are structured").

Every tool call goes through ``ToolRegistry.call``, which turns whatever the
use case raised into a ``ToolFailure`` carrying a ``ToolErrorPayload``. The
transport (``mcp_server``) only renders that payload; it never inspects
exceptions itself.

Messages are chosen for the caller, not copied from the exception: a use-case
message written for people (``ContextUnavailableError``) is passed on, but an
error that may wrap a driver's text (hosts, users, SQL) is replaced by a fixed
sentence and logged server-side with its traceback.
"""

import logging

from pydantic import ValidationError

from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    PlanningUnavailableError,
)
from parkmind.tools.contracts import ToolErrorCode, ToolErrorPayload

logger = logging.getLogger(__name__)

_UNAVAILABLE = (
    "A data source this tool needs is unavailable right now; try again later."
)
_INTERNAL = "The tool failed unexpectedly; the server logged the details."


class ToolFailure(Exception):
    """An anticipated tool failure, already in its published shape."""

    def __init__(self, payload: ToolErrorPayload) -> None:
        super().__init__(payload.message)
        self.payload = payload

    @classmethod
    def of(
        cls,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, list[str]] | None = None,
    ) -> "ToolFailure":
        return cls(
            ToolErrorPayload(
                code=code, message=message, retryable=retryable, details=details or {}
            )
        )


def invalid_arguments(exc: ValidationError) -> ToolFailure:
    """The request did not match the tool's input model: name the fields, never their values."""
    fields = sorted(
        {
            ".".join(str(part) for part in error["loc"]) or "(request)"
            for error in exc.errors()
        }
    )
    return ToolFailure.of(
        ToolErrorCode.INVALID_ARGUMENT,
        "The arguments do not match the tool's input schema.",
        details={"fields": fields},
    )


def to_tool_failure(tool_name: str, exc: Exception) -> ToolFailure:
    """Map an exception raised while running ``tool_name`` to its structured failure."""
    if isinstance(exc, ToolFailure):
        return exc
    if isinstance(exc, ContextUnavailableError):
        logger.info("Tool %s: park data unavailable: %s", tool_name, exc)
        return ToolFailure.of(ToolErrorCode.UNAVAILABLE, str(exc), retryable=True)
    if isinstance(exc, PlanningUnavailableError):
        logger.warning("Tool %s: a port failed: %s", tool_name, exc)
        return ToolFailure.of(ToolErrorCode.UNAVAILABLE, _UNAVAILABLE, retryable=True)
    logger.exception("Tool %s raised an unexpected exception", tool_name, exc_info=exc)
    return ToolFailure.of(ToolErrorCode.INTERNAL, _INTERNAL)
