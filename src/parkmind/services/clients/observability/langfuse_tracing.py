"""Langfuse tracing, masked before anything leaves the process (ADR 0003, C19).

Tracing is opt-in: ``PARKMIND_TRACING=langfuse`` plus ``LANGFUSE_PUBLIC_KEY``,
``LANGFUSE_SECRET_KEY`` and ``LANGFUSE_BASE_URL``. ``langfuse_callbacks`` then
returns one LangChain ``CallbackHandler``. The composition root (``server.py``)
hands it to ``ConversationService``, which passes it in every graph run's config,
so each node and each LLM call is traced and the thread is the Langfuse session.

The Langfuse client is built with ``mask=mask_for_trace``. The SDK runs it over
every input, output and metadata value it records, the handler's included, before
the span is exported. The mask is strict (the decision of 2026-10-14):
- every chat message keeps only its role, type, name and id. Its text, tool calls
  and extra fields are masked, for guests' turns (C19: raw user turns are masked)
  and for AI replies alike, since those can echo the guests' words or names;
- a Command's ``resume``: an answer to a clarifying question resumes the graph as plain
  text, alone or under its interrupt id, and that text is the guests' words;
- correction text, ``Guest.label``, the confirmation echo and explanations (they
  can carry a label), every accessibility field and every stated height
  (``height_cm``, and the ``height:g2: 122`` entry of a pending confirmation) are
  masked wherever they appear;
- an error's text: a paused graph's ``Interrupt`` carries its question, which repeats
  the guests' unmatched attraction names, so a status message keeps only the error's type;
- the elicit prompt's guest block is masked if it shows up as a plain string.

Everything else -- constraints, plans, events, check results, ids -- is traced as
it is: the typed outputs are what a trace is for (section 42). A mask that fails
masks everything: the SDK replaces the value with a fixed string.

LangSmith tracing stays refused (``config.tracing.tracing_guard``): its tracer is
added by ``langchain-core`` from the environment and never passes through this mask.
"""

import dataclasses
import functools
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

MASKED = "[masked]"
"""What a masked value becomes in a trace."""

_MESSAGE_KEPT = frozenset({"role", "type", "name", "id"})
_MASKED_KEYS = frozenset(
    {
        "correction",
        "label",
        "echo",
        "explanation",
        "accessibility",
        "accessibility_requirements",
        "mobility_requirements",
        "ride_restrictions",
        "daily_walking_limit_minutes",
        "height_cm",
        "guest_height_cm",
    }
)
_HEIGHT_ENTRY = "height:"
"""A pending confirmation for a stated height reads ``height:g2: 122``."""
_GUEST_BLOCK = "Guest messages, oldest first"
"""The line that opens the guests' text in the elicit prompt (``AnthropicExtractor``)."""


class TracingConfigError(RuntimeError):
    """``PARKMIND_TRACING=langfuse`` is set, but the Langfuse settings are incomplete."""


def mask_for_trace(*, data: Any, **_: Any) -> Any:
    """``data`` as it may be exported: the rules in the module docstring."""
    return _masked(data, depth=0)


def _masked(value: Any, *, depth: int) -> Any:
    if depth > 50:  # a cycle, or a structure too deep to read: hide it
        return MASKED
    deeper = depth + 1
    if isinstance(value, str):
        if _GUEST_BLOCK in value:
            return MASKED
        if value.startswith(_HEIGHT_ENTRY):
            return f"{value.partition(': ')[0]}: {MASKED}"
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(
        value, BaseModel
    ):  # a LangChain message too: it dumps to a message dict
        return _masked(value.model_dump(mode="json"), depth=deeper)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _masked(
            {f.name: getattr(value, f.name) for f in dataclasses.fields(value)},
            depth=deeper,
        )
    if isinstance(value, Mapping):
        if _looks_like_message(value):
            return {k: v for k, v in value.items() if k in _MESSAGE_KEPT}
        return {
            k: MASKED
            if k in _MASKED_KEYS
            else _masked_resume(v, depth=deeper)
            if k == "resume"
            else _masked(v, depth=deeper)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_masked(v, depth=deeper) for v in value]
    # Anything else (an arbitrary object) is traced by its type name only.
    return f"<{type(value).__name__}>"


def _masked_resume(value: Any, *, depth: int) -> Any:
    """A Command's ``resume``: a bare string, alone or under its interrupt id, is an answer."""
    if isinstance(value, str):
        return MASKED
    if isinstance(value, Mapping):
        return {
            k: MASKED if isinstance(v, str) else _masked(v, depth=depth + 1)
            for k, v in value.items()
        }
    return _masked(value, depth=depth)


def _looks_like_message(value: Mapping[str, Any]) -> bool:
    """A message as a dict: the handler's ``{"role", "content"}``, or a dumped message."""
    return "content" in value and ("role" in value or "type" in value)


_client: Any = None


def langfuse_callbacks(env: Mapping[str, str] | None = None) -> list[Any]:
    """The callbacks for a graph run: ``[]`` unless Langfuse tracing is switched on."""
    env = os.environ if env is None else env
    mode = env.get("PARKMIND_TRACING", "").strip().lower()
    if mode in ("", "off", "false", "0", "none"):
        return []
    if mode != "langfuse":
        raise TracingConfigError(
            f"PARKMIND_TRACING={mode!r} is not supported: use 'langfuse' or 'off'"
        )
    public_key = env.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = env.get("LANGFUSE_SECRET_KEY", "").strip()
    base_url = env.get("LANGFUSE_BASE_URL", "").strip()
    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", public_key),
            ("LANGFUSE_SECRET_KEY", secret_key),
            ("LANGFUSE_BASE_URL", base_url),
        )
        if not value
    ]
    if missing:
        raise TracingConfigError(
            f"PARKMIND_TRACING=langfuse needs {', '.join(missing)} (see .env.example)"
        )
    from langfuse import Langfuse

    global _client
    if _client is None:
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            mask=mask_for_trace,
            environment=env.get("PARKMIND_TRACING_ENVIRONMENT", "local").strip()
            or "local",
        )
    return [_masked_handler_class()(public_key=public_key)]


@functools.cache
def _masked_handler_class() -> Any:
    """The SDK handler, with the error text it records reduced to the error's type.

    The SDK stores ``str(error)`` as the observation's status message and never runs
    the mask over it. A paused graph raises an ``Interrupt`` whose text holds its
    payload, the question and the guests' unmatched attraction names.
    """
    from langfuse.langchain import CallbackHandler

    class MaskedCallbackHandler(CallbackHandler):
        def _get_error_level_and_status_message(
            self, error: BaseException
        ) -> tuple[Any, str]:
            level, _ = super()._get_error_level_and_status_message(error)
            return level, type(error).__name__

    return MaskedCallbackHandler


def flush_tracing() -> None:
    """Send what is buffered and stop the exporter (process shutdown, scripts)."""
    global _client
    if _client is not None:
        _client.shutdown()
        _client = None
