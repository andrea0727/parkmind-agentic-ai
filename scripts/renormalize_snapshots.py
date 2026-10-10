"""Rebuilds stored snapshots from their raw payloads (backlog P0-11, Architecture 41 C21).

    poetry run python scripts/renormalize_snapshots.py                 # rows built by an older normalizer
    poetry run python scripts/renormalize_snapshots.py --all           # every snapshot
    poetry run python scripts/renormalize_snapshots.py --snapshot-id S # one (repeatable)
    poetry run python scripts/renormalize_snapshots.py --dry-run       # list what would be rebuilt

Run it after a change bumps NORMALIZER_VERSION (services/clients/normalization.py).
Only ``live_context`` and ``normalizer_version`` are rewritten; the raw payload,
sources and retrieval time never change. A snapshot that can't be rebuilt is
reported and skipped. Exit code 1 if any failed, or on a database problem.
"""

import argparse
import os
import sys
from collections.abc import Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from parkmind.services.clients.normalization import NORMALIZER_VERSION
from parkmind.services.clients.postgres.connection import (
    DATABASE_PROBLEMS,
    connect,
    describe_database_problem,
)
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.use_cases.renormalize_snapshots import (
    all_snapshot_ids,
    renormalize_snapshots,
    stale_snapshot_ids,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all", action="store_true", help="every snapshot, not only outdated ones"
    )
    selection.add_argument(
        "--snapshot-id",
        action="append",
        default=[],
        help="a specific snapshot (repeatable)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list the snapshots, change nothing"
    )
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    args = parser.parse_args(argv)

    try:
        with connect(args.database_url) as conn:
            snapshots = PostgresSnapshotRepository(conn)
            if args.snapshot_id:
                ids = list(args.snapshot_id)
            elif args.all:
                ids = all_snapshot_ids(snapshots)
            else:
                ids = stale_snapshot_ids(snapshots)
            if args.dry_run:
                print(
                    f"{len(ids)} snapshot(s) selected (normalizer version {NORMALIZER_VERSION}):"
                )
                for snapshot_id in ids:
                    print(f"  {snapshot_id}")
                return 0
            report = renormalize_snapshots(
                snapshots, PostgresIdMappingRepository(conn), ids
            )
    except DATABASE_PROBLEMS as exc:
        print(describe_database_problem(exc), file=sys.stderr)
        return 1

    print(
        f"normalizer version {NORMALIZER_VERSION}: "
        f"{report.count('rebuilt')} rebuilt, {report.count('unchanged')} unchanged, "
        f"{report.count('failed')} failed"
    )
    for outcome in report.failed:
        print(f"  failed {outcome.snapshot_id}: {outcome.reason}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
