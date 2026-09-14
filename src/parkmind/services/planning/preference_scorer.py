"""
PreferenceScorer — converts resolved group preferences into a utility score
per attraction/stop.

utility = preference - queue_penalty - walking_penalty - risk_penalty - change_penalty
"""


class PreferenceScorer:
    def score(self, resolved_preferences, context) -> dict[str, float]:
        raise NotImplementedError
