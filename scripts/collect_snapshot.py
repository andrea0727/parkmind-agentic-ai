"""Collects park snapshots into Postgres (backlog P0-11).

    poetry run alembic -c database/alembic.ini upgrade head   # once
    poetry run python scripts/collect_snapshot.py             # one snapshot, then exit
    poetry run python scripts/collect_snapshot.py --loop      # every 5 minutes until Ctrl+C
    poetry run python scripts/collect_snapshot.py --latest    # latest valid snapshot and its age

A run inside a collection window that is already stored writes nothing and
calls no provider, so a scheduler may fire this as often as it likes. The
README's "Snapshots" section shows the Windows Task Scheduler / cron setup.

Exit codes: 0 collected (or already collected), 1 database problem, 2 park
data unavailable (no snapshot was stored).
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg

from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.postgres.connection import connect
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.ports import RepositoryUnavailableError
from parkmind.services.use_cases.collect_snapshot import (
    CollectResult,
    SnapshotCollector,
)
from parkmind.services.use_cases.latest_snapshot import latest_valid_snapshot

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"
SCHEMA_HINT = "Run first: poetry run alembic -c database/alembic.ini upgrade head"

logger = logging.getLogger("collect_snapshot")


def _report(result: CollectResult) -> str:
    if not result.created:
        return f"already collected {result.snapshot_id} (nothing written)"
    context = result.live_context
    assert context is not None
    parts = [
        f"collected {result.snapshot_id}",
        f"sources={','.join(source.value for source in result.data_sources)}",
        f"statuses={len(context.statuses)} waits={len(context.waits)} weather_hours={len(context.weather)}",
    ]
    if result.issues:
        parts.append(f"mapping_issues={len(result.issues)}")
    if result.degraded:
        parts.append(f"degraded={'; '.join(result.degraded)}")
    return " | ".join(parts)


def collect_once(
    collector_factory: "CollectorFactory", database_url: str | None, now: datetime
) -> int:
    try:
        with connect(database_url) as conn:
            result = collector_factory(conn).collect(now=now)
    except ThemeParksClientError as exc:
        print(f"park data unavailable, no snapshot stored: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        print(f"The snapshot schema is missing or outdated. {SCHEMA_HINT}", file=sys.stderr)
        return 1
    except (psycopg.OperationalError, RepositoryUnavailableError):
        print("PostgreSQL is not reachable. Start it with `docker compose up -d`.", file=sys.stderr)
        return 1
    print(_report(result))
    return 0


def show_latest(database_url: str | None, now: datetime) -> int:
    try:
        with connect(database_url) as conn:
            snapshots = PostgresSnapshotRepository(conn)
            latest = latest_valid_snapshot(snapshots, now=now)
            stored_any = bool(snapshots.recent_ids(1))
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        print(f"The snapshot schema is missing or outdated. {SCHEMA_HINT}", file=sys.stderr)
        return 1
    except (psycopg.OperationalError, RepositoryUnavailableError):
        print("PostgreSQL is not reachable. Start it with `docker compose up -d`.", file=sys.stderr)
        return 1
    if latest is None:
        if stored_any:
            print(
                "snapshots are stored, but none of the recent ones is valid any more: "
                "run scripts/renormalize_snapshots.py --all"
            )
        else:
            print("no valid snapshot stored yet")
        return 0
    minutes = latest.age.total_seconds() / 60
    status = "fresh" if latest.fresh else "STALE"
    print(
        f"latest {latest.live_context.snapshot_id} | retrieved "
        f"{latest.live_context.retrieved_at.astimezone(PARK_TZ):%Y-%m-%d %H:%M %Z} | "
        f"age {minutes:.0f} min | {status}"
    )
    for snapshot_id in latest.skipped:
        print(f"  skipped (no longer valid, re-normalize it): {snapshot_id}")
    return 0


class CollectorFactory:
    """Builds a collector on a given connection; the clients are reused across runs."""

    def __init__(self, parks: ThemeParksClient, weather: OpenMeteoClient) -> None:
        self._parks, self._weather = parks, weather

    def __call__(self, conn: psycopg.Connection) -> SnapshotCollector:
        return SnapshotCollector(
            self._parks,
            self._weather,
            PostgresSnapshotRepository(conn),
            PostgresIdMappingRepository(conn),
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    parks: ThemeParksClient | None = None,
    weather: OpenMeteoClient | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--loop", action="store_true", help="keep collecting until interrupted")
    parser.add_argument("--latest", action="store_true", help="show the latest valid snapshot and its age, collect nothing")
    parser.add_argument("--interval", type=int, default=300, help="seconds between runs with --loop (default 300)")
    parser.add_argument("--park-id", default=MAGIC_KINGDOM, help="ThemeParks entity id (default Magic Kingdom)")
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    args = parser.parse_args(argv)
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")

    if args.latest:
        return show_latest(args.database_url, datetime.now(PARK_TZ))

    factory = CollectorFactory(parks or ThemeParksClient(args.park_id), weather or OpenMeteoClient())
    if not args.loop:
        return collect_once(factory, args.database_url, datetime.now(PARK_TZ))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        while True:
            code = collect_once(factory, args.database_url, datetime.now(PARK_TZ))
            if code == 1:
                return code  # a database problem won't fix itself between runs
            time.sleep(timedelta(seconds=args.interval).total_seconds())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
