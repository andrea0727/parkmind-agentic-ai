"""Langfuse is opt-in and fails closed; every graph run carries the handler (ADR 0003)."""

from typing import Any

import pytest
from langfuse.langchain import CallbackHandler

from parkmind.graph.conversation import ConversationService
from parkmind.services.clients.observability import langfuse_tracing
from parkmind.services.clients.observability.langfuse_tracing import (
    TracingConfigError,
    langfuse_callbacks,
    mask_for_trace,
)

KEYS = {
    "PARKMIND_TRACING": "langfuse",
    "LANGFUSE_PUBLIC_KEY": "pk-test",
    "LANGFUSE_SECRET_KEY": "sk-test",
    "LANGFUSE_BASE_URL": "http://localhost:1",
}


@pytest.fixture(autouse=True)
def fresh_client(monkeypatch):
    monkeypatch.setattr(langfuse_tracing, "_client", None)
    yield
    langfuse_tracing.flush_tracing()


@pytest.mark.parametrize("value", [None, "", "off", "OFF", "false", "none"])
def test_tracing_is_off_unless_switched_on(value):
    env = {} if value is None else {"PARKMIND_TRACING": value}
    assert langfuse_callbacks(env) == []


@pytest.mark.parametrize("missing", sorted(set(KEYS) - {"PARKMIND_TRACING"}))
def test_switched_on_without_its_settings_refuses_to_start(missing):
    env = {k: v for k, v in KEYS.items() if k != missing}

    with pytest.raises(TracingConfigError, match=missing):
        langfuse_callbacks(env)


def test_an_unknown_tracing_backend_is_refused():
    with pytest.raises(TracingConfigError, match="langsmith"):
        langfuse_callbacks({**KEYS, "PARKMIND_TRACING": "langsmith"})


def test_switched_on_it_returns_one_handler_on_a_masked_client(monkeypatch):
    built: list[dict[str, Any]] = []

    class RecordingLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

        def shutdown(self) -> None:
            pass

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", RecordingLangfuse)

    [handler] = langfuse_callbacks(KEYS)

    assert isinstance(handler, CallbackHandler)
    [kwargs] = built
    assert kwargs["mask"] is mask_for_trace
    assert (kwargs["public_key"], kwargs["base_url"]) == (
        "pk-test",
        "http://localhost:1",
    )


class _RecordingGraph:
    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []

    def invoke(self, graph_input: Any, config: Any) -> dict[str, Any]:
        self.configs.append(config)
        return {"messages": []}


def test_every_run_carries_the_callbacks_with_the_thread_as_session():
    graph = _RecordingGraph()
    handler = object()
    service = ConversationService(graph, _RecordingGraph(), callbacks=[handler])
    thread_id = service.start()

    service.say(thread_id, "hello")

    [config] = graph.configs
    assert config["callbacks"] == [handler]
    assert config["metadata"] == {
        "langfuse_session_id": thread_id,
        "langfuse_tags": ["initial"],
    }
    assert config["configurable"] == {"thread_id": thread_id}


def test_without_callbacks_the_run_config_is_unchanged():
    graph = _RecordingGraph()
    service = ConversationService(graph, _RecordingGraph())
    thread_id = service.start()

    service.say(thread_id, "hello")

    assert graph.configs == [{"configurable": {"thread_id": thread_id}}]


def test_an_errors_text_is_reduced_to_its_type_in_the_status_message():
    from parkmind.services.clients.observability.langfuse_tracing import (
        _masked_handler_class,
    )

    handler = _masked_handler_class()(public_key="pk-test")

    _, status = handler._get_error_level_and_status_message(
        ValueError("No encontré 'zebra-canary' entre las atracciones")
    )

    assert status == "ValueError"
