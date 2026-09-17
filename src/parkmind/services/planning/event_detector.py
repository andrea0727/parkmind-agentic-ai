"""
Pure event-detection logic: given two snapshots, decide whether a
significant Event occurred and how severe. The *polling* that fetches
snapshots lives in services/use_cases/monitor_events.py — keep that I/O
concern separate from this pure logic.
"""

from parkmind.core.contracts import Event


def detect_events(previous_snapshot, current_snapshot) -> list[Event]:
    raise NotImplementedError
