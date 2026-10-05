"""check_accessibility use case (P0-26a): fail-closed matching against published notices."""

import ast
import inspect
import logging

import pytest
from factories import accessibility

from parkmind.core.contracts import (
    AccessibilityCheck,
    AccessibilityRequirements,
    MobilityRequirement,
    RideRestriction,
)
from parkmind.services.clients.knowledge import InMemoryKnowledgeStore
from parkmind.services.use_cases import check_accessibility as module
from parkmind.services.use_cases.check_accessibility import (
    check_accessibility,
    guest_flags,
    notice_coverage,
)

HIGH_G = RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE
MOTION = RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY
EXPECTANT = RideRestriction.NOT_RECOMMENDED_EXPECTANT
TRANSFER = RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR
SERVICE_ANIMAL = RideRestriction.USES_SERVICE_ANIMAL


def _store(**notices: list[RideRestriction]) -> InMemoryKnowledgeStore:
    return InMemoryKnowledgeStore(notices, corpus_version="2026-09-test")


def _guest(
    *,
    ride: list[RideRestriction] | None = None,
    mobility: list[MobilityRequirement] | None = None,
) -> AccessibilityRequirements:
    return accessibility(
        guest_id="g1",
        daily_walking_limit_minutes=None,
        ride_restrictions=ride or [],
        mobility_requirements=mobility or [],
    )


def test_check_returns_accessibility_check_with_corpus_provenance() -> None:
    result = check_accessibility(_guest(ride=[HIGH_G]), "a1", _store(a1=[]))

    assert isinstance(result, AccessibilityCheck)
    assert (result.attraction_id, result.guest_id) == ("a1", "g1")
    assert result.eligible is True
    assert result.conflicting_requirement is None
    assert result.provenance == {
        "source": "park_safety_notice",
        "version": "2026-09-test",
        "notice_on_file": True,
    }


def test_no_notice_on_file_is_not_eligible_for_a_guest_with_flags() -> None:
    result = check_accessibility(_guest(ride=[HIGH_G]), "uncovered", _store(a1=[]))

    assert result.eligible is False
    assert result.conflicting_requirement is None
    assert result.provenance["notice_on_file"] is False


def test_no_notice_on_file_is_not_eligible_for_a_wheelchair_user() -> None:
    result = check_accessibility(
        _guest(mobility=[MobilityRequirement.WHEELCHAIR]), "uncovered", _store()
    )

    assert result.eligible is False


def test_guest_without_flags_is_eligible_without_a_notice() -> None:
    result = check_accessibility(_guest(), "uncovered", _store())

    assert result.eligible is True
    assert result.provenance["notice_on_file"] is False


def test_ride_restriction_conflict_is_reported() -> None:
    result = check_accessibility(
        _guest(ride=[HIGH_G]), "a1", _store(a1=[HIGH_G, EXPECTANT])
    )

    assert result.eligible is False
    assert result.conflicting_requirement == HIGH_G.value


def test_a_notice_without_the_guests_flags_is_eligible() -> None:
    result = check_accessibility(
        _guest(ride=[MOTION]), "a1", _store(a1=[HIGH_G, EXPECTANT])
    )

    assert result.eligible is True


@pytest.mark.parametrize(
    "mobility",
    [
        MobilityRequirement.WHEELCHAIR,
        MobilityRequirement.ECV,
        MobilityRequirement.STROLLER_AS_WHEELCHAIR,
    ],
)
def test_wheelchair_conflicts_with_transfer_required(
    mobility: MobilityRequirement,
) -> None:
    result = check_accessibility(
        _guest(mobility=[mobility]), "a1", _store(a1=[TRANSFER])
    )

    assert result.eligible is False
    assert result.conflicting_requirement == TRANSFER.value


def test_wheelchair_user_is_eligible_where_they_may_remain_seated() -> None:
    result = check_accessibility(
        _guest(mobility=[MobilityRequirement.WHEELCHAIR]), "a1", _store(a1=[EXPECTANT])
    )

    assert result.eligible is True


def test_limited_walking_never_conflicts_with_a_notice() -> None:
    guest = _guest(mobility=[MobilityRequirement.LIMITED_WALKING])

    assert guest_flags(guest) == frozenset()
    assert (
        check_accessibility(guest, "a1", _store(a1=list(RideRestriction))).eligible
        is True
    )


def test_service_animal_conflict() -> None:
    result = check_accessibility(
        _guest(ride=[SERVICE_ANIMAL]), "a1", _store(a1=[SERVICE_ANIMAL])
    )

    assert result.eligible is False
    assert result.conflicting_requirement == SERVICE_ANIMAL.value


def test_conflict_order_is_deterministic() -> None:
    guest = _guest(ride=[SERVICE_ANIMAL, EXPECTANT, HIGH_G])
    store = _store(a1=[SERVICE_ANIMAL, HIGH_G, EXPECTANT])

    first = check_accessibility(guest, "a1", store)
    second = check_accessibility(guest, "a1", store)

    assert first == second
    assert (
        first.conflicting_requirement == HIGH_G.value
    )  # first in RideRestriction order


def test_check_emits_no_log_records(caplog: pytest.LogCaptureFixture) -> None:
    guest = _guest(ride=[HIGH_G], mobility=[MobilityRequirement.WHEELCHAIR])
    store = _store(a1=[HIGH_G])

    with caplog.at_level(logging.DEBUG):
        check_accessibility(guest, "a1", store)
        check_accessibility(guest, "uncovered", store)

    assert caplog.records == []


def test_check_module_imports_no_repository_or_logging() -> None:
    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }

    assert imported <= {
        "collections.abc",
        "dataclasses",
        "parkmind.core.contracts",
        "parkmind.services.ports",
    }


def test_notice_coverage_lists_covered_and_missing() -> None:
    store = _store(a1=[HIGH_G], a2=[], retired=[TRANSFER])

    coverage = notice_coverage(store, ["a3", "a2", "a1"])

    assert coverage.corpus_version == "2026-09-test"
    assert coverage.covered == ("a1", "a2")
    assert coverage.missing == ("a3",)
    assert coverage.not_in_catalog == ("retired",)
    assert coverage.ratio == pytest.approx(2 / 3)


def test_notice_coverage_of_an_empty_catalog_is_complete() -> None:
    coverage = notice_coverage(_store(a1=[]), [])

    assert coverage.covered == coverage.missing == ()
    assert coverage.ratio == 1.0
