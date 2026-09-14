"""
Use case: the polling job. Fetches the latest snapshot via the clients,
runs services.planning.event_detector against it, returns significant
Events. This owns "how often do we check" — detection logic itself stays
in services/planning/event_detector.py.

TODO: a simple asyncio loop or APScheduler job is enough for month 1 — no
message queue needed.
"""


class MonitorEventsUseCase:
    def __init__(self, themeparks_client, open_meteo_client, postgres_repository, event_detector_fn):
        self.themeparks_client = themeparks_client
        self.open_meteo_client = open_meteo_client
        self.postgres_repository = postgres_repository
        self.event_detector_fn = event_detector_fn

    def execute(self) -> list:
        raise NotImplementedError
