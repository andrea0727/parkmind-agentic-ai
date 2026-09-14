"""
ThemeParks.wiki client — real waits, attraction status, schedules.
API docs: https://api.themeparks.wiki/docs/v1
"""


class ThemeParksClient:
    def get_live_waits(self, attraction_ids: list[str]) -> dict[str, float]:
        raise NotImplementedError

    def get_schedule(self, attraction_id: str) -> dict:
        raise NotImplementedError

    def get_attraction_status(self, attraction_id: str) -> str:
        raise NotImplementedError
