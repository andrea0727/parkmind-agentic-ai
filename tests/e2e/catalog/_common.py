"""Building blocks shared by the catalog modules: message fragments and canned extractions."""

from datetime import timedelta
from typing import Any

from e2e.kit.env import e2e_now

H = "Adults are 170 cm. "

HAPPY = (
    (
        "We are four people, two adults and two kids. We definitely want TRON and Space Mountain. "
        "The kids don't like very intense rides. My father can't walk long distances."
    ),
    (
        "We leave the park at 10 PM. The adults are 175 cm and 168 cm tall, the kids are 130 cm and 125 cm. "
        "We want lunch around 1 PM."
    ),
)


def soon() -> str:
    """The departure time 25 minutes after the pretended park time."""
    return (e2e_now() + timedelta(minutes=25)).strftime("%H:%M")


def adult(height_cm: float | None = None, **sensitivities: str) -> dict[str, Any]:
    return _guest("adult", height_cm, sensitivities)


def child(height_cm: float | None = None, **sensitivities: str) -> dict[str, Any]:
    return _guest("child", height_cm, sensitivities)


def _guest(
    role: str, height_cm: float | None, sensitivities: dict[str, str]
) -> dict[str, Any]:
    guest: dict[str, Any] = {"role": role}
    if height_cm is not None:
        guest["height_cm"] = height_cm
    if sensitivities:
        guest["sensitivities"] = [
            {"kind": kind.upper(), "level": level}
            for kind, level in sensitivities.items()
        ]
    return guest


def extraction(
    guests: list[dict[str, Any]],
    *,
    party_size: int | None = None,
    must_do: list[str] | None = None,
    avoid: list[str] | None = None,
    departure: str | None = "22:00",
    lunch: tuple[str, str] | None = None,
    walking_budget: int | None = None,
    wheelchair: int | None = None,
) -> dict[str, Any]:
    """What a correct extractor returns for the scenario, in the ElicitationExtraction shape."""
    out: dict[str, Any] = {
        "party_size": party_size if party_size is not None else len(guests),
        "guests": guests,
    }
    if must_do:
        out["must_do"] = must_do
    if avoid:
        out["avoid"] = avoid
    if departure:
        out["departure_time"] = departure
    if lunch:
        out["lunch_window"] = {"start": lunch[0], "end": lunch[1]}
    if walking_budget is not None:
        out["party_walking_budget_minutes"] = walking_budget
    if wheelchair is not None:
        out["accessibility"] = [
            {"guest_ref": wheelchair, "mobility_requirements": ["WHEELCHAIR"]}
        ]
    return out
