"""Saved parties of known accounts, so the planner does not ask who "guest 1" is.

A household is a default, never a fact: it reaches the extractor as a message the guests did
not type, later messages correct it, and heights and accessibility are never stored here, so
they still go through the guests' own words and the confirmation step.
"""

from dataclasses import dataclass

from parkmind.core.contracts import GuestRole

_MAX_MEMBERS = 12


class HouseholdFileError(ValueError):
    """The households file is unreadable or malformed."""


@dataclass(frozen=True)
class Member:
    role: GuestRole
    label: str | None = None
    preferences: str = ""


@dataclass(frozen=True)
class Household:
    members: tuple[Member, ...]

    def as_message(self) -> str:
        lines = [
            (
                "SAVED PARTY of the signed-in account (not typed just now; use it as the "
                "default party and let the guests' own messages correct it):"
            )
        ]
        for i, m in enumerate(self.members, start=1):
            parts = [f"{i}. {m.role.value}"]
            if m.label:
                parts.append(f'called "{m.label}"')
            if m.preferences:
                parts.append(f"preferences: {m.preferences}")
            lines.append(", ".join(parts))
        return "\n".join(lines)


def _member(username: str, raw: object) -> Member:
    if not isinstance(raw, dict):
        raise HouseholdFileError(f"{username}: every party member must be an object")
    unknown = set(raw) - {"role", "label", "preferences"}
    if unknown:
        raise HouseholdFileError(
            f"{username}: unknown member fields {sorted(unknown)} "
            "(heights and accessibility are never stored)"
        )
    try:
        role = GuestRole(raw.get("role"))
    except ValueError:
        raise HouseholdFileError(f"{username}: role must be adult or child") from None
    label, preferences = raw.get("label"), raw.get("preferences", "")
    if label is not None and not isinstance(label, str):
        raise HouseholdFileError(f"{username}: label must be text")
    if not isinstance(preferences, str):
        raise HouseholdFileError(f"{username}: preferences must be text")
    return Member(role=role, label=label or None, preferences=preferences.strip())


def parse_households(raw: object) -> dict[str, Household]:
    if not isinstance(raw, dict):
        raise HouseholdFileError("the households file must be an object keyed by username")
    result: dict[str, Household] = {}
    for username, entry in raw.items():
        party = entry.get("party") if isinstance(entry, dict) else None
        if not isinstance(party, list) or not 0 < len(party) <= _MAX_MEMBERS:
            raise HouseholdFileError(f"{username}: party must list 1 to {_MAX_MEMBERS} members")
        result[username] = Household(tuple(_member(username, m) for m in party))
    return result
