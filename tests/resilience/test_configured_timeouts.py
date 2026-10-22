"""Configured timeouts and retry counts reach every client, and a dead provider fails after exactly that many tries."""

from typing import Any, ClassVar

import httpx
import pytest

from parkmind.agents import explain_agent
from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.explain_agent import AnthropicNarrator
from parkmind.config.settings import settings
from parkmind.services.clients import _retry
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.open_meteo_errors import (
    OpenMeteoNotFoundError,
    OpenMeteoUnavailableError,
)
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_errors import (
    ThemeParksNotFoundError,
    ThemeParksUnavailableError,
)
from resilience.res_faults import Reply

PARK_ID = "park-1"


class _RecordedHttpClient(httpx.Client):
    instances: ClassVar[list["_RecordedHttpClient"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.configured = kwargs
        _RecordedHttpClient.instances.append(self)
        super().__init__(**kwargs)


@pytest.fixture
def http_clients(monkeypatch: pytest.MonkeyPatch) -> list[_RecordedHttpClient]:
    _RecordedHttpClient.instances = []
    monkeypatch.setattr(httpx, "Client", _RecordedHttpClient)
    return _RecordedHttpClient.instances


class _RecordedChat:
    created: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        _RecordedChat.created.append(kwargs)

    def bind_tools(self, *args: Any, **kwargs: Any) -> "_RecordedChat":
        return self


@pytest.fixture
def chat_models(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import langchain_anthropic

    _RecordedChat.created = []
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordedChat)
    return _RecordedChat.created


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(_retry.time, "sleep", slept.append)
    return slept


# --- HTTP clients -----------------------------------------------------------------------------


def test_the_park_data_client_passes_its_timeout_to_the_http_client(
    http_clients: list[_RecordedHttpClient],
) -> None:
    ThemeParksClient(PARK_ID, timeout=3.5)

    assert http_clients[0].configured["timeout"] == 3.5


def test_the_weather_client_passes_its_timeout_to_the_http_client(
    http_clients: list[_RecordedHttpClient],
) -> None:
    OpenMeteoClient(timeout=2.5)

    assert http_clients[0].configured["timeout"] == 2.5


def test_the_clients_default_to_a_bounded_timeout_and_three_attempts(
    http_clients: list[_RecordedHttpClient],
) -> None:
    ThemeParksClient(PARK_ID)
    weather = OpenMeteoClient()

    assert [c.configured["timeout"] for c in http_clients] == [10.0, 10.0]
    assert (weather.max_retries, weather.backoff_seconds) == (3, 0.5)


def test_the_weather_client_exposes_the_attempts_and_backoff_it_was_given() -> None:
    weather = OpenMeteoClient(max_retries=5, backoff_seconds=0.25)

    assert (weather.max_retries, weather.backoff_seconds) == (5, 0.25)


@pytest.mark.parametrize("attempts", [0, -1])
def test_a_client_without_a_single_attempt_is_refused(attempts: int) -> None:
    with pytest.raises(ValueError):
        ThemeParksClient(PARK_ID, max_retries=attempts)
    with pytest.raises(ValueError):
        OpenMeteoClient(max_retries=attempts)


def _always(status: int, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    return httpx.MockTransport(handler)


def _timing_out(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("no answer", request=request)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_the_park_data_client_gives_up_after_the_configured_attempts(
    status: int, sleeps: list[float]
) -> None:
    seen: list[httpx.Request] = []
    client = ThemeParksClient(
        PARK_ID, transport=_always(status, seen), max_retries=4, backoff_seconds=0.5
    )

    with pytest.raises(ThemeParksUnavailableError):
        client.get_catalog()

    assert len(seen) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_the_park_data_client_gives_up_after_repeated_timeouts(
    sleeps: list[float],
) -> None:
    seen: list[httpx.Request] = []
    client = ThemeParksClient(
        PARK_ID, transport=_timing_out(seen), max_retries=2, backoff_seconds=0.1
    )

    with pytest.raises(ThemeParksUnavailableError):
        client.get_catalog()

    assert len(seen) == 2
    assert sleeps == [0.1]


def test_the_park_data_client_does_not_retry_a_missing_resource(
    sleeps: list[float],
) -> None:
    seen: list[httpx.Request] = []
    client = ThemeParksClient(PARK_ID, transport=_always(404, seen), max_retries=4)

    with pytest.raises(ThemeParksNotFoundError):
        client.get_catalog()

    assert len(seen) == 1
    assert sleeps == []


def test_a_single_attempt_means_no_retry_and_no_sleep(sleeps: list[float]) -> None:
    seen: list[httpx.Request] = []
    client = ThemeParksClient(PARK_ID, transport=_always(503, seen), max_retries=1)

    with pytest.raises(ThemeParksUnavailableError):
        client.get_catalog()

    assert len(seen) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_the_weather_client_gives_up_after_the_configured_attempts(
    status: int, sleeps: list[float]
) -> None:
    seen: list[httpx.Request] = []
    client = OpenMeteoClient(
        transport=_always(status, seen), max_retries=3, backoff_seconds=0.5
    )

    with pytest.raises(OpenMeteoUnavailableError):
        client.get_hourly_forecast()

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_the_weather_client_gives_up_after_repeated_timeouts(
    sleeps: list[float],
) -> None:
    seen: list[httpx.Request] = []
    client = OpenMeteoClient(
        transport=_timing_out(seen), max_retries=2, backoff_seconds=0.2
    )

    with pytest.raises(OpenMeteoUnavailableError):
        client.get_hourly_forecast()

    assert len(seen) == 2
    assert sleeps == [0.2]


def test_the_weather_client_does_not_retry_a_missing_resource(
    sleeps: list[float],
) -> None:
    seen: list[httpx.Request] = []
    client = OpenMeteoClient(transport=_always(404, seen), max_retries=4)

    with pytest.raises(OpenMeteoNotFoundError):
        client.get_hourly_forecast()

    assert len(seen) == 1
    assert sleeps == []


# --- model clients ---------------------------------------------------------------------------


def test_the_extractor_bounds_each_request_and_leaves_retries_to_the_counted_loop(
    chat_models: list[dict[str, Any]],
) -> None:
    extractor = AnthropicExtractor(api_key="test-key")

    extractor._runnable()

    assert chat_models[0]["timeout"] == 60
    assert chat_models[0]["max_retries"] == 0


def test_the_narrator_uses_the_configured_timeout_by_default(
    chat_models: list[dict[str, Any]],
) -> None:
    AnthropicNarrator(api_key="test-key")._runnable()

    assert chat_models[0]["timeout"] == settings.EXPLAIN_TIMEOUT_SECONDS
    assert chat_models[0]["max_retries"] == 0
    assert explain_agent.EXPLAIN_TIMEOUT_SECONDS == settings.EXPLAIN_TIMEOUT_SECONDS


def test_the_narrator_honours_the_timeout_and_retries_it_is_given(
    chat_models: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk_retries: list[int] = []

    def fake_invoke(runnable: Any, messages: Any, *, retries: int, budget: Any) -> Any:
        sdk_retries.append(retries)
        return Reply([{"args": {}}])

    monkeypatch.setattr(explain_agent, "invoke_counted", fake_invoke)
    narrator = AnthropicNarrator(api_key="test-key", timeout=5.0, max_retries=2)

    narrator.narrate("{}")

    assert chat_models[0]["timeout"] == 5.0
    assert chat_models[0]["max_retries"] == 0
    assert sdk_retries == [2]


def test_the_narrator_without_a_key_fails_at_once_instead_of_waiting_on_the_network() -> (
    None
):
    narrator = AnthropicNarrator(api_key="")

    with pytest.raises(explain_agent.NarratorUnavailableError):
        narrator.narrate("{}")
