"""Process-level guard: the planner's state lives in this process, so there is exactly one."""

import os
import sys
from collections.abc import Mapping, Sequence

WORKER_ENV_VARS = ("WEB_CONCURRENCY", "PARKMIND_WORKERS")


class MultipleWorkersError(RuntimeError):
    """More than one worker was asked for."""


def _count(value: str | None) -> int:
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


def _workers_in(argv: Sequence[str]) -> int:
    for i, arg in enumerate(argv):
        if arg == "--workers" and i + 1 < len(argv):
            return _count(argv[i + 1])
        if arg.startswith("--workers="):
            return _count(arg.split("=", 1)[1])
    return 1


def ensure_single_worker(
    env: Mapping[str, str] | None = None, argv: Sequence[str] | None = None
) -> None:
    """Refuse to start with several workers.

    Sessions (the checkpointer) and ``session_only`` data are in-memory and per process;
    a second worker would answer with a thread or consent the first one never saw.
    """
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    asked = max(
        [_count(env.get(name)) for name in WORKER_ENV_VARS] + [_workers_in(argv)]
    )
    if asked > 1:
        raise MultipleWorkersError(
            f"{asked} workers requested, but ParkMind keeps sessions and session-only data "
            "in memory: run exactly one worker (or put a shared store behind it first)."
        )
