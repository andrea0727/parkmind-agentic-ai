from collections.abc import Iterator

import pytest

from observability.obs_support import Tracing, tracing_to


@pytest.fixture
def tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[Tracing]:
    with tracing_to(monkeypatch) as active:
        yield active


@pytest.fixture(autouse=True)
def no_ambient_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
        "LANGCHAIN_TRACING",
        "LANGCHAIN_TRACING_V2",
        "PARKMIND_TRACING",
    ):
        monkeypatch.delenv(name, raising=False)
