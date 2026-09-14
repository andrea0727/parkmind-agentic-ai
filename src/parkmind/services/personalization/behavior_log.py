"""
BehaviorLog — records BehaviorSignal (models/behavior_signal.py) and
answers "what has this guest accepted/rejected so far".

Feeds services/evaluation/metrics/personalization.py (Proposal Acceptance
Rate, Rejection Reason Distribution).
"""

from parkmind.models.behavior_signal import BehaviorSignal


class BehaviorLog:
    def record(self, signal: BehaviorSignal) -> None:
        raise NotImplementedError

    def history_for(self, guest_id: str) -> list[BehaviorSignal]:
        raise NotImplementedError
