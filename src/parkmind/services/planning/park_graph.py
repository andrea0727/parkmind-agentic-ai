"""
ParkGraph — the planning space: attractions, shows, coordinates, opening
hours, walking edges. TODO: load from a static JSON for month 1, no real
graph DB needed.
"""


class ParkGraph:
    def walk_minutes(self, a: str, b: str) -> float:
        raise NotImplementedError

    def open_at(self, attraction_id: str, t) -> bool:
        raise NotImplementedError

    def showtimes(self, show_id: str) -> list:
        raise NotImplementedError
