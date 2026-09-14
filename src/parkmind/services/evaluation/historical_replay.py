"""Replays a historical day from snapshots, simulates events, triggers
replanning, measures outcome. Uses services.planning directly, never the
LLM — results must be deterministic and reproducible."""


def replay_day(snapshots: list, scenario_events: list) -> dict:
    raise NotImplementedError
