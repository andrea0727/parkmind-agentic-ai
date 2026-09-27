"""P0-11 Done-when 5 (local execution path) and the re-normalization *command*.

The scripts are loaded from scripts/ and their ``main`` is called in-process
with fixture-backed clients -- the scripts themselves contain no test hooks.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from capture import Provider

REPO_ROOT = Path(__file__).resolve().parents[3]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect(url: str, provider: Provider) -> int:
    return _script("collect_snapshot").main(
        ["--database-url", url], parks=provider.parks(), weather=provider.weather()
    )


def test_one_shot_collect_exits_zero_and_reports(
    migrated_database_url: str, conn, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _collect(migrated_database_url, Provider()) == 0
    first = capsys.readouterr().out
    assert first.startswith("collected snap_") and "sources=themeparks_wiki,open_meteo" in first

    assert _collect(migrated_database_url, Provider()) == 0  # same window
    assert capsys.readouterr().out.startswith("already collected snap_")


def test_park_outage_exits_two_and_stores_nothing(
    migrated_database_url: str, conn, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _collect(migrated_database_url, Provider(parks_status=503)) == 2
    assert "no snapshot stored" in capsys.readouterr().err
    row = conn.execute("SELECT count(*) AS n FROM snapshots").fetchone()
    assert row is not None and row["n"] == 0


def test_missing_schema_is_explained(empty_database_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _collect(empty_database_url, Provider()) == 1
    assert "alembic" in capsys.readouterr().err


def test_renormalize_command_rebuilds_and_reports(
    migrated_database_url: str, conn, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _collect(migrated_database_url, Provider()) == 0
    capsys.readouterr()
    conn.execute("UPDATE snapshots SET live_context = %s::jsonb", (json.dumps({"broken": True}),))
    conn.commit()  # the script runs on its own connection

    renormalize = _script("renormalize_snapshots").main
    assert renormalize(["--database-url", migrated_database_url, "--all", "--dry-run"]) == 0
    assert "1 snapshot(s) selected" in capsys.readouterr().out

    assert renormalize(["--database-url", migrated_database_url, "--all"]) == 0
    assert "1 rebuilt, 0 unchanged, 0 failed" in capsys.readouterr().out
    assert renormalize(["--database-url", migrated_database_url, "--all"]) == 0
    assert "0 rebuilt, 1 unchanged, 0 failed" in capsys.readouterr().out


def test_renormalize_command_exits_one_when_a_snapshot_fails(
    migrated_database_url: str, conn, capsys: pytest.CaptureFixture[str]
) -> None:
    renormalize = _script("renormalize_snapshots").main

    assert renormalize(["--database-url", migrated_database_url, "--snapshot-id", "ghost"]) == 1
    assert "failed ghost: no such snapshot" in capsys.readouterr().out
