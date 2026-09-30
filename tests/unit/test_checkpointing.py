"""Checkpointer construction: msgpack allowlist coverage.

Guards the fix for LangGraph's permissive default (warn-but-allow on an
unregistered custom type): every non-excluded contract type must be
allow-listed, and AccessibilityRequirements must stay excluded [C19].
"""

from parkmind.core import contracts
from parkmind.graph.checkpointing import _checkpoint_safe_types, default_checkpointer


def test_checkpoint_safe_types_excludes_accessibility_requirements():
    names = {t.__name__ for t in _checkpoint_safe_types()}
    assert "AccessibilityRequirements" not in names


def test_checkpoint_safe_types_covers_every_model_and_enum_contract():
    import inspect
    from enum import Enum

    from pydantic import BaseModel

    expected = {
        name
        for name in contracts.__all__
        if name != "AccessibilityRequirements"
        and inspect.isclass(obj := getattr(contracts, name))
        and issubclass(obj, BaseModel | Enum)
    }
    actual = {t.__name__ for t in _checkpoint_safe_types()}
    assert actual == expected


def test_default_checkpointer_builds_a_working_memory_saver():
    saver = default_checkpointer()
    assert saver is not None
