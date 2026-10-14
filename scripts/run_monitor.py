"""Runs the event monitor: collect a snapshot, then detect park events (backlog P0-33).

    poetry run python scripts/run_monitor.py                 # collect + detect once
    poetry run python scripts/run_monitor.py --loop          # every 5 minutes until Ctrl+C
    poetry run python scripts/run_monitor.py --no-collect    # detect on what is stored

The database comes from the DATABASE_URL environment variable.

Each run takes a snapshot exactly as ``scripts/collect_snapshot.py`` does, then
compares the two most recent stored snapshots and prints the park-wide events
(down, available, wait spike, rain). They are *candidates*: severity and
whether a party must replan are decided per party by EventPolicy when the
replanning graph handles them (P0-34). A reading the provider has not updated
within the freshness window never produces an event.

Exit codes: 0 done, 1 database problem, 2 park data unavailable (no snapshot
was stored; nothing is detected that run).
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Sequence
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from collect_snapshot import MAGIC_KINGDOM, CollectorFactory, collect_once

from parkmind.core.contracts import Event
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.use_cases.monitor_events import (
    MonitorEventsUseCase,
    MonitorResult,
)
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
)


def describe(event: Event) -> str:
    when = event.timestamp.astimezone(PARK_TZ).strftime("%H:%M")
    if event.affected_hours:
        hours = ", ".join(
            h.astimezone(PARK_TZ).strftime("%H:%M") for h in event.affected_hours
        )
        return f"{when} {event.type.value} hours={hours} p={event.confidence:.2f}"
    detail = (
        f" +{event.delta_minutes:.0f} min" if event.delta_minutes is not None else ""
    )
    return f"{when} {event.type.value} {event.attraction_id}{detail}"


def report(result: MonitorResult) -> str:
    if result.previous_snapshot_id is None:
        return "fewer than two valid snapshots stored: nothing to compare yet"
    header = (
        f"compared {result.previous_snapshot_id} -> {result.current_snapshot_id}: "
        f"{len(result.candidates)} event(s)"
    )
    lines = [header]
    lines += [f"  {describe(e)}" for e in result.candidates]
    return "\n".join(lines)


def detect_once(deps_factory: DepsFactory, now: datetime) -> int:
    try:
        result = MonitorEventsUseCase(deps_factory).detect(now=now)
    except PlanningUnavailableError as exc:
        print(f"cannot read stored snapshots: {exc}", file=sys.stderr)
        return 1
    print(report(result))
    return 0


def run_once(
    factory: CollectorFactory | None, deps_factory: DepsFactory, now: datetime
) -> int:
    if factory is not None:
        code = collect_once(factory, now)
        if code != 0:
            return code
    return detect_once(deps_factory, now)


def main(
    argv: Sequence[str] | None = None,
    *,
    parks: ThemeParksClient | None = None,
    weather: OpenMeteoClient | None = None,
    deps_factory: DepsFactory | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--loop", action="store_true", help="keep running until interrupted"
    )
    parser.add_argument(
        "--no-collect", action="store_true", help="detect on the stored snapshots only"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="seconds between runs with --loop (default 300)",
    )
    parser.add_argument(
        "--park-id",
        default=MAGIC_KINGDOM,
        help="ThemeParks entity id (default Magic Kingdom)",
    )
    args = parser.parse_args(argv)
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")
    deps_factory = deps_factory or default_planning_deps

    factory = (
        None
        if args.no_collect
        else CollectorFactory(
            parks or ThemeParksClient(args.park_id), weather or OpenMeteoClient()
        )
    )
    if not args.loop:
        return run_once(factory, deps_factory, datetime.now(PARK_TZ))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        while True:
            code = run_once(factory, deps_factory, datetime.now(PARK_TZ))
            if code == 1:
                return code  # a database problem won't fix itself between runs
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
