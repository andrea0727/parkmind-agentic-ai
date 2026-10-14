"""Tracing guard [C19]: no trace leaves the process until it can be masked.

``langchain-core`` adds a LangSmith tracer to every run by itself when tracing is
switched on through the environment (``LANGSMITH_TRACING=true``, or the legacy
``LANGCHAIN_TRACING_V2=true``), with no code change. Every graph run would then send
the guests' raw messages -- in ``ParkMindState.messages`` and in the elicit prompt --
to a third-party cloud, while C19 requires raw user turns to be masked in traces.

Until P1-18 installs a tested masking hook (ADR 0003), building a graph refuses to
run with tracing switched on. Langfuse needs explicit code to trace (a callback
handler), so its keys alone send nothing and are not refused here.
"""

import os
from collections.abc import Mapping

TRACING_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
"""The variables ``langsmith`` reads to switch tracing on (``LANGSMITH_*`` first)."""


class UnmaskedTracingError(RuntimeError):
    """Tracing is switched on, but nothing masks the guests' raw messages yet."""


def tracing_guard(environ: Mapping[str, str] | None = None) -> None:
    """Raise ``UnmaskedTracingError`` if any tracing switch is on (``"true"``, any case)."""
    env = os.environ if environ is None else environ
    switched_on = [
        name for name in TRACING_SWITCHES if env.get(name, "").strip().lower() == "true"
    ]
    if switched_on:
        pronoun = "it" if len(switched_on) == 1 else "them"
        raise UnmaskedTracingError(
            f"{', '.join(switched_on)} would send the guests' raw messages to LangSmith "
            f"unmasked [C19]. Set {pronoun} to false: tracing waits for P1-18's masking "
            "(docs/decisions/0003-observability-langsmith-vs-langfuse.md)."
        )
