"""The catalog and the schedule are cached in the database and fetched from the provider when missing."""

from datetime import date
from typing import Any

import factories
import pytest
from planning_support import NOW, PARK, PlanningAttractionRepository, make_deps

from parkmind.core.contracts import Attraction, Park
from parkmind.services.clients.themeparks_errors import ThemeParksUnavailableError
from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    PlanningDeps,
    PlanningUnavailableError,
    load_catalog,
    load_park,
    open_deps,
)


class _Store(PlanningAttractionRepository):
    """A repository that remembers what is saved to it."""

    def __init__(self, attractions: list[Attraction] | None = None, park: Park | None = None) -> None:
        super().__init__(attractions=attractions if attractions is not None else [], park=park)
        self.saved_catalog: list[Attraction] | None = None
        self.saved_park: Park | None = None

    def save_catalog(self, park_id: str, attractions: Any) -> None:
        self.saved_catalog = list(attractions)
        self._attractions = list(attractions)

    def save_schedule(self, park: Park) -> None:
        self.saved_park = park
        self._park = park


class _Provider:
    def __init__(self, catalog: list[Attraction] | None = None, error: Exception | None = None) -> None:
        self._catalog = catalog if catalog is not None else [factories.attraction()]
        self._error = error
        self.catalog_calls = 0
        self.schedule_calls: list[date] = []

    def get_catalog(self) -> list[Attraction]:
        self.catalog_calls += 1
        if self._error:
            raise self._error
        return self._catalog

    def get_schedule(self, on_date: date) -> Park:
        self.schedule_calls.append(on_date)
        if self._error:
            raise self._error
        return PARK


def _deps(store: _Store, provider: _Provider | None) -> PlanningDeps:
    deps = make_deps(attractions=store)
    return PlanningDeps(**{**deps.__dict__, "park_data": provider})


def test_a_stored_catalog_and_schedule_never_call_the_provider():
    provider = _Provider()
    deps = _deps(_Store([factories.attraction()], PARK), provider)

    assert load_catalog(deps)
    assert load_park(deps, NOW) == PARK
    assert provider.catalog_calls == 0 and provider.schedule_calls == []


def test_an_empty_store_is_filled_once_from_the_provider():
    store, provider = _Store(), _Provider()
    deps = _deps(store, provider)

    first = load_catalog(deps)
    second = load_catalog(deps)

    assert first == second == store.saved_catalog
    assert provider.catalog_calls == 1


def test_a_missing_schedule_is_fetched_for_the_park_local_date_and_stored():
    store, provider = _Store(), _Provider()
    deps = _deps(store, provider)

    assert load_park(deps, NOW) == PARK
    assert load_park(deps, NOW) == PARK

    assert store.saved_park == PARK
    assert provider.schedule_calls == [NOW.date()]


def test_without_a_provider_missing_data_fails_closed():
    deps = _deps(_Store(), None)

    with pytest.raises(ContextUnavailableError):
        load_catalog(deps)
    with pytest.raises(ContextUnavailableError):
        load_park(deps, NOW)


def test_an_empty_provider_catalog_fails_closed_and_stores_nothing():
    store = _Store()
    deps = _deps(store, _Provider(catalog=[]))

    with pytest.raises(ContextUnavailableError):
        load_catalog(deps)
    assert store.saved_catalog is None


def test_a_provider_failure_surfaces_as_planning_unavailable():
    deps = _deps(_Store(), _Provider(error=ThemeParksUnavailableError("down")))

    with pytest.raises(PlanningUnavailableError), open_deps(lambda: _ctx(deps)) as opened:
        load_catalog(opened)


def _ctx(deps: PlanningDeps) -> Any:
    from contextlib import nullcontext

    return nullcontext(deps)
