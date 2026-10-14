"""Process-level guard: the planner's state lives in this process, so there is exactly one."""

import os
import sys
from collections.abc import Mapping, Sequence

WORKER_ENV_VARS = ("WEB_CONCURRENCY", "PARKMIND_WORKERS")
_WORKER_FLAGS = ("--workers", "-w")


class MultipleWorkersError(RuntimeError):
    """More than one worker was asked for, or the count cannot be read."""


def _count(value: str | None) -> int:
    if not value:
        return 1
    try:
        return int(value)
    except ValueError:
        raise MultipleWorkersError(
            f"cannot read the worker count {value!r}: ParkMind runs exactly one worker"
        ) from None


def _workers_in(argv: Sequence[str]) -> int:
    """``--workers N``, ``--workers=N``, ``-w N`` and ``-wN`` (uvicorn and gunicorn)."""
    for i, arg in enumerate(argv):
        if arg in _WORKER_FLAGS and i + 1 < len(argv):
            return _count(argv[i + 1])
        if arg.startswith("--workers="):
            return _count(arg.split("=", 1)[1])
        if arg.startswith("-w") and len(arg) > 2 and not arg.startswith("--"):
            return _count(arg[2:])
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
