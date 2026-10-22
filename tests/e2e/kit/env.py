"""Paths, the fixed park clock and the opt-in loading of the local .env."""

import os
from datetime import datetime, time
from pathlib import Path

from parkmind.core.contracts import PARK_TZ

REPO = Path(__file__).resolve().parents[3]
RAW_DIR = Path(os.environ.get("E2E_RAW_DIR", REPO / "docs" / "e2e" / "raw"))
REPORT_DIR = REPO / "docs" / "e2e"
RUNS_DIR = REPO / "build" / "e2e" / "runs"

EDGE_URL = os.environ.get(
    "E2E_EDGE_DATABASE_URL",
    "postgresql://parkmind:parkmind@localhost:5432/parkmind_edge",
)
BAD_URL = os.environ.get(
    "E2E_BAD_DATABASE_URL", "postgresql://parkmind:parkmind@localhost:5999/parkmind"
)


def e2e_now() -> datetime:
    """Fixed park-time clock (E2E_NOW=HH:MM, default 10:00) so runs never depend on the time of day."""
    hh, mm = os.environ.get("E2E_NOW", "10:00").split(":")
    return datetime.combine(
        datetime.now(PARK_TZ).date(), time(int(hh), int(mm)), tzinfo=PARK_TZ
    )


def load_live_env() -> None:
    """Load the repository .env into the process; only live runs call this, never at import."""
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
