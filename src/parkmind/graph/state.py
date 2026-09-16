"""
ParkMindState — the LangGraph state shape.

current_plan and candidate_plan are deliberately separate fields so a plan
can never be treated as active before it's been through
interrupt()/approval.
"""

from typing import Literal, TypedDict


class ParkMindState(TypedDict, total=False):
    thread_id: str
    messages: list

    constraints: dict | None
    guest_profiles: list[dict]
    resolved_preferences: dict | None

    live_context: dict | None

    current_plan: dict | None
    candidate_plan: dict | None

    event: dict | None
    check_result: dict | None
    diff: dict | None
    proposal: dict | None

    approval: Literal["pending", "approved", "rejected", "edited"] | None
    behavior_signals: list[dict]

    iteration: int
