"""Checkpointer construction for graphs built on ParkMindState.

LangGraph's default msgpack serde is currently permissive: an unregistered
custom type (Pydantic models, enums) is still deserialized, with a warning
that this will tighten in a future release (LANGGRAPH_STRICT_MSGPACK locks
it down today). Allow-listing every checkpoint-safe contract type up front
keeps ParkMindState checkpoints resolving correctly once that default
changes, instead of silently degrading a field into a raw dict.

AccessibilityRequirements is deliberately excluded: it must never be
checkpointed [C19], so if it ever ended up in a payload we want
deserialization blocked, not silently reconstructed.
"""

import inspect
from enum import Enum

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from parkmind.core import contracts

_EXCLUDED_FROM_ALLOWLIST = {"AccessibilityRequirements"}


def _checkpoint_safe_types() -> list[type]:
    types_: list[type] = []
    for name in contracts.__all__:
        if name in _EXCLUDED_FROM_ALLOWLIST:
            continue
        obj = getattr(contracts, name)
        if inspect.isclass(obj) and issubclass(obj, BaseModel | Enum):
            types_.append(obj)
    return types_


def default_checkpointer() -> MemorySaver:
    """An in-memory checkpointer whose serde allow-lists every §33 contract
    type ParkMindState can hold (except AccessibilityRequirements).

    In-memory only: no orchestration entrypoint wires a durable (e.g.
    Postgres-backed) checkpointer yet, so persistence across process
    restarts is out of scope here.
    """
    serde = JsonPlusSerializer(allowed_msgpack_modules=_checkpoint_safe_types())
    return MemorySaver(serde=serde)
