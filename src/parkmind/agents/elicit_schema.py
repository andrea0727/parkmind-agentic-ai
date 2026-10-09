"""Structured output of the ``elicit`` step.

The LLM is only a classifier and extractor: it fills this schema and nothing
more. Everything is a closed enum or a number -- there is no free-text field
for health information, so an accessibility statement can only surface as a
derived flag (no medical inference) [C15, C19]. Consent and retention are not
in the schema at all: they are given by the human, never by the model.

Statements are split into two disjoint groups:

* soft preferences (``ExtractedGuest``) -- tradeable, scored by the planner;
* hard constraints (must-do, avoid, fixed times, walking budget, a guest's
  stated height and every ``ExtractedAccessibility`` flag) -- never traded off,
  so each one must be confirmed by the human before it can reach the checker.
  Height is the input of the height-minimum safety rule, so it is hard even
  though it sits on ``ExtractedGuest``.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from parkmind.core.contracts import (
    AttractionCategory,
    GuestRole,
    MobilityRequirement,
    PlanningPace,
    PlanningStyle,
    RideRestriction,
    SensitivityKind,
    SensitivityLevel,
)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

Classification = Literal["hard", "soft"]


def normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def _check_hhmm(value: str | None, field: str) -> str | None:
    if value is not None and not _HHMM.match(value):
        # Name the field, never the value: the value may be the guest's own words,
        # and this text is sent back to the model as the repair hint.
        raise ValueError(f"{field} must be a 24h 'HH:MM' time")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedSensitivity(_Strict):
    kind: SensitivityKind
    level: SensitivityLevel


class ExtractedAffinity(_Strict):
    theme: str = Field(min_length=1)
    affinity: float = Field(ge=-1, le=1)


class ExtractedGuest(_Strict):
    """One person in the party: soft preferences, plus the stated height (hard)."""

    label: str | None = None
    role: GuestRole | None = None
    height_cm: float | None = Field(
        default=None,
        ge=50,
        le=250,
        description="Height in cm, only if the guests stated it (1 ft = 30.48 cm, 1 in = 2.54 cm)",
    )
    pace: PlanningPace | None = None
    planning_style: PlanningStyle | None = None
    queue_tolerance: float | None = Field(default=None, ge=0, le=1)
    walking_tolerance: float | None = Field(default=None, ge=0, le=1)
    sensitivities: list[ExtractedSensitivity] = Field(default_factory=list)
    preferred_categories: list[AttractionCategory] = Field(default_factory=list)
    avoided_categories: list[AttractionCategory] = Field(default_factory=list)
    thematic_affinity: list[ExtractedAffinity] = Field(default_factory=list)


class ExtractedAccessibility(_Strict):
    """Derived accessibility flags for one guest. Closed enums and numbers only."""

    guest_ref: int = Field(ge=1, description="1-based position in `guests`")
    daily_walking_limit_minutes: int | None = Field(default=None, ge=0)
    rest_frequency_minutes: int | None = Field(default=None, ge=0)
    mobility_requirements: list[MobilityRequirement] = Field(default_factory=list)
    heat_sensitivity: bool = False
    ride_restrictions: list[RideRestriction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_a_flag(self) -> "ExtractedAccessibility":
        if not (
            self.daily_walking_limit_minutes is not None
            or self.rest_frequency_minutes is not None
            or self.mobility_requirements
            or self.heat_sensitivity
            or self.ride_restrictions
        ):
            raise ValueError("an accessibility entry must set at least one flag")
        return self


class ExtractedWindow(_Strict):
    start: str
    end: str

    @model_validator(mode="after")
    def _valid(self) -> "ExtractedWindow":
        _check_hhmm(self.start, "lunch_window.start")
        _check_hhmm(self.end, "lunch_window.end")
        if self.end <= self.start:
            raise ValueError("window end must be after its start")
        return self


class ElicitationExtraction(_Strict):
    party_size: int | None = Field(default=None, gt=0)
    guests: list[ExtractedGuest] = Field(default_factory=list)
    must_do: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    departure_time: str | None = None
    lunch_window: ExtractedWindow | None = None
    party_walking_budget_minutes: int | None = Field(default=None, ge=0)
    accessibility: list[ExtractedAccessibility] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "ElicitationExtraction":
        _check_hhmm(self.departure_time, "departure_time")
        if (
            self.guests
            and self.party_size is not None
            and self.party_size != len(self.guests)
        ):
            raise ValueError(
                f"party_size={self.party_size} but {len(self.guests)} guests were listed"
            )
        for entry in self.accessibility:
            if entry.guest_ref > len(self.guests):
                raise ValueError(
                    f"accessibility guest_ref {entry.guest_ref} has no matching guest"
                )
        return self

    def classified_items(self) -> dict[str, Classification]:
        """Every extracted statement keyed by a stable id, labelled hard or soft.

        This is the unit the classification accuracy is measured on.
        """
        items: dict[str, Classification] = {}
        for name in self.must_do:
            items[f"must_do:{normalize_name(name)}"] = "hard"
        for name in self.avoid:
            items[f"avoid:{normalize_name(name)}"] = "hard"
        if self.departure_time is not None:
            items["departure_time"] = "hard"
        if self.lunch_window is not None:
            items["lunch_window"] = "hard"
        if self.party_walking_budget_minutes is not None:
            items["walking_budget"] = "hard"
        for entry in self.accessibility:
            ref = f"g{entry.guest_ref}"
            if entry.daily_walking_limit_minutes is not None:
                items[f"accessibility:{ref}:WALKING_LIMIT"] = "hard"
            if entry.rest_frequency_minutes is not None:
                items[f"accessibility:{ref}:REST_FREQUENCY"] = "hard"
            for mobility in entry.mobility_requirements:
                items[f"accessibility:{ref}:{mobility.value}"] = "hard"
            if entry.heat_sensitivity:
                items[f"accessibility:{ref}:HEAT_SENSITIVITY"] = "hard"
            for restriction in entry.ride_restrictions:
                items[f"accessibility:{ref}:{restriction.value}"] = "hard"

        for index, guest in enumerate(self.guests, start=1):
            ref = f"g{index}"
            if guest.height_cm is not None:
                items[f"height:{ref}"] = "hard"
            if guest.pace is not None:
                items[f"pace:{ref}"] = "soft"
            if guest.planning_style is not None:
                items[f"planning_style:{ref}"] = "soft"
            if guest.queue_tolerance is not None:
                items[f"queue_tolerance:{ref}"] = "soft"
            if guest.walking_tolerance is not None:
                items[f"walking_tolerance:{ref}"] = "soft"
            for sensitivity in guest.sensitivities:
                items[f"sensitivity:{ref}:{sensitivity.kind.value}"] = "soft"
            for category in guest.preferred_categories:
                items[f"preferred:{ref}:{category.value}"] = "soft"
            for category in guest.avoided_categories:
                items[f"avoided:{ref}:{category.value}"] = "soft"
            for affinity in guest.thematic_affinity:
                items[f"affinity:{ref}:{normalize_name(affinity.theme)}"] = "soft"
        return items
