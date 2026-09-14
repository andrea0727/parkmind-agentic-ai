"""
ForecastService — interchangeable wait-time forecasting strategy.
Month-1 scope: API forecast + historical-profile fallback only. No ML —
see services/personalization/preference_learner.py for where ML actually
belongs in this project instead.
"""


class ForecastService:
    def forecast_wait(self, attraction_id: str, at_time) -> float:
        raise NotImplementedError
