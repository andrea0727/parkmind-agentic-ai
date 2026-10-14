"""Tracing guard [C19]: with tracing on and nothing masking it, no graph is built (ADR 0003)."""

import pytest
from elicit_support import make_names
from langgraph.checkpoint.memory import MemorySaver

from parkmind.config.tracing import (
    TRACING_SWITCHES,
    UnmaskedTracingError,
    tracing_guard,
)
from parkmind.graph.elicitation_graph import build_elicitation_graph
from parkmind.graph.initial_planning_graph import (
    build_initial_planning_graph,
    build_planning_stage,
)
from parkmind.graph.replanning_graph import build_replanning_graph


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    for name in TRACING_SWITCHES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", TRACING_SWITCHES)
@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_any_tracing_switch_set_to_true_is_refused(name, value):
    with pytest.raises(UnmaskedTracingError, match=name):
        tracing_guard({name: value})


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_tracing_switched_off_or_unset_is_allowed(value):
    tracing_guard({"LANGSMITH_TRACING": value})
    tracing_guard({})


def test_the_error_names_every_switch_that_is_on():
    with pytest.raises(UnmaskedTracingError) as raised:
        tracing_guard({"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"})

    message = str(raised.value)
    assert "LANGSMITH_TRACING, LANGCHAIN_TRACING_V2" in message
    assert "Set them to false" in message


@pytest.mark.parametrize("name", TRACING_SWITCHES)
@pytest.mark.parametrize(
    "value", ["true", "True", "TRUE", " true", "1", "yes", "on", "t", "y", "enabled"]
)
def test_the_guard_refuses_whatever_the_installed_langsmith_would_trace_with(
    monkeypatch, name, value
):
    """If a langsmith upgrade starts tracing on "1" or "yes", this fails until the
    guard follows: the guard must never be narrower than langsmith itself."""
    from langsmith import utils as ls_utils

    monkeypatch.setenv(name, value)
    ls_utils.get_env_var.cache_clear()
    try:
        traces = ls_utils.tracing_is_enabled() is True
    finally:
        ls_utils.get_env_var.cache_clear()

    if traces:
        with pytest.raises(UnmaskedTracingError):
            tracing_guard({name: value})


def test_langfuse_keys_alone_are_allowed():
    """Langfuse traces only through an explicit callback handler, which nothing installs."""
    tracing_guard({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})


@pytest.mark.parametrize(
    "build",
    [
        lambda: build_elicitation_graph(checkpointer=MemorySaver(), names=make_names()),
        lambda: build_initial_planning_graph(
            checkpointer=MemorySaver(), names=make_names()
        ),
        lambda: build_replanning_graph(MemorySaver()),
        build_planning_stage,
    ],
    ids=["elicitation", "initial_planning", "replanning", "planning_stage"],
)
@pytest.mark.parametrize("switch", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
def test_no_graph_is_built_while_tracing_would_ship_raw_guest_turns(
    monkeypatch, build, switch
):
    monkeypatch.setenv(switch, "true")

    with pytest.raises(UnmaskedTracingError):
        build()


def test_every_graph_builds_with_tracing_off():
    build_elicitation_graph(checkpointer=MemorySaver(), names=make_names())
    build_initial_planning_graph(checkpointer=MemorySaver(), names=make_names())
    build_replanning_graph(MemorySaver())


def test_the_api_server_refuses_to_start_while_tracing_is_on(monkeypatch):
    """The HTTP entrypoint builds its graphs through the guarded builders."""
    import server

    # build_app loads .env, which could set a demo switch again.
    monkeypatch.setattr(server, "load_dotenv", lambda: None)
    for name in server.DEMO_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PARKMIND_AUTH_DISABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(UnmaskedTracingError):
        server.build_app()
