"""
BehaviorLog — records BehaviorEntry and answers "what has this guest
accepted/rejected so far".

Feeds services/evaluation/metrics/personalization.py (Proposal Acceptance
Rate, Rejection Reason Distribution).
"""

from parkmind.core.contracts import BehaviorEntry


class BehaviorLog:
    def record(self, signal: BehaviorEntry) -> None:
        raise NotImplementedError

    def history_for(self, guest_id: str) -> list[BehaviorEntry]:
        raise NotImplementedError
