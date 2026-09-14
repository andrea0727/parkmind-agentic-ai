"""
data.* tools: get_live_waits, get_attraction_status, get_schedule,
get_showtimes, get_weather, get_walking_time, get_attraction_info.

Each function calls a client in services/clients/ and wraps the result
with Provenance (models/provenance.py).
"""


def get_live_waits(attraction_ids: list[str]) -> dict:
    raise NotImplementedError


def get_attraction_status(attraction_id: str) -> dict:
    raise NotImplementedError


def get_weather(at_time) -> dict:
    raise NotImplementedError
