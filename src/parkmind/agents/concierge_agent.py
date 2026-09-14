"""
Concierge Agent — the main interaction point with the guest. Understands
natural language into PartyConstraints + GuestProfiles, orchestrates tool
calls, manages the approval flow, explains the planner's output.

This is the piece with the most design judgment in the system — start with
"understand" (structured extraction) since it's independently testable
before any tool wiring exists.
"""


class ConciergeAgent:
    def extract_constraints(self, user_message: str) -> dict:
        raise NotImplementedError

    def explain(self, plan: dict, diff: dict | None = None) -> str:
        raise NotImplementedError
