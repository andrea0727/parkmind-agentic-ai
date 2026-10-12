"""scripts/run_monitor.py prints the monitor's candidates (P0-33)."""

import importlib.util
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest
from capture import NOW, Provider
from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository
from planning_support import factory_for, make_deps
from test_monitor_events import SECOND, _second_live

from parkmind.services.use_cases.collect_snapshot import SnapshotCollector

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_monitor.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_monitor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _deps(collections):
    snapshots, id_mappings = InMemorySnapshotRepository(), InMemoryIdMappingRepository()
    for at, live in collections:
        provider = Provider(live=live)
        SnapshotCollector(
            provider.parks(), provider.weather(), snapshots, id_mappings
        ).collect(now=at)
    return replace(make_deps(snapshots=snapshots), id_mappings=id_mappings)


def test_detect_once_prints_the_fresh_closure(script, capsys):
    deps = _deps([(NOW, None), (SECOND, _second_live())])

    code = script.detect_once(factory_for(deps), SECOND + timedelta(minutes=1))

    out = capsys.readouterr().out
    assert code == 0
    assert "1 event(s)" in out and "ATTRACTION_DOWN 796b0a25" in out


def test_with_one_snapshot_there_is_nothing_to_compare(script, capsys):
    deps = _deps([(NOW, None)])

    assert script.detect_once(factory_for(deps), NOW + timedelta(minutes=1)) == 0
    assert "nothing to compare" in capsys.readouterr().out


def test_the_interval_has_a_floor(script):
    with pytest.raises(SystemExit):
        script.main(["--loop", "--interval", "10"])


def test_the_database_url_reaches_detection(script, monkeypatch):
    """Review #86 (S5): ``--database-url`` is not only for the collector."""
    deps = _deps([(NOW, None)])
    seen: list[str | None] = []

    def fake_default(database_url=None):
        seen.append(database_url)
        return factory_for(deps)()

    monkeypatch.setattr(script, "default_planning_deps", fake_default)

    code = script.main(["--no-collect", "--database-url", "postgresql://elsewhere"])

    assert code == 0 and seen == ["postgresql://elsewhere"]
