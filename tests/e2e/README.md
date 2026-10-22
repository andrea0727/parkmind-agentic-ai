# E2E on real ports (layer 1)

Runs the initial planning graph and the replanning graph against real dependencies and judges what happened per scenario. The scenario catalog (`catalog/`) is shared with the workflow layer, which replays the same journeys offline.

## Structure

- `model.py`: `Scenario`, `Criteria`, `Offline`, `FollowUp`, `Fault`.
- `catalog/`: the 36 scenarios, one module per area. `catalog.SCENARIOS`, `catalog.by_id`, `catalog.ordered`.
- `kit/`: the driver that runs a scenario through a `Runtime`, the judges (`verdicts.py`), fault injection, the stage spy and recording extractor, the live runtime, the report and matrix renderers.
- `cli.py`: `list`, `capture`, `report`, `matrix`.
- `test_catalog.py`, `test_verdicts.py`: offline checks of the catalog and the judges. `test_scenarios.py`: the live run, marked `live`.

## Requires real credentials

| What | How |
|---|---|
| Anthropic LLM | `ANTHROPIC_API_KEY` in `.env` (model: `ELICIT_MODEL`, default Claude Haiku 4.5) |
| Postgres | `DATABASE_URL` in `.env`, migrated (`alembic upgrade head`); `docker compose up -d` gives you one |
| Throwaway Postgres with an empty catalog | `E2E_EDGE_DATABASE_URL` (default `postgresql://parkmind:parkmind@localhost:5432/parkmind_edge`), migrated. Used by `proveedor_caido_db_vacia` and `happy_catalogo_vacio`; the latter fills its catalog, so recreate the database to run it again |
| ThemeParks, Open-Meteo | Public internet access; no key |

A full run makes about 35 real LLM calls. Optional variables: `E2E_NOW` (park time the run pretends it is, default `10:00`), `E2E_RAW_DIR`, `E2E_BAD_DATABASE_URL` (a port nobody listens on, to simulate a database outage).

## Run

```bash
poetry run pytest tests/e2e --live                  # judge every live scenario
poetry run pytest tests/e2e --live -k espanol       # one scenario

# or capture facts to docs/e2e/raw and build the report afterwards
python scripts/e2e/capture.py                       # all, skipping those already captured
python scripts/e2e/capture.py espanol grupo_de_1    # only these
FORCE=1 python scripts/e2e/capture.py               # capture everything again
SCENARIO_TIMEOUT_SECONDS=600 python scripts/e2e/capture.py   # total time allowed per scenario (default 300); one that runs out is saved as a ScenarioTimeoutError
python scripts/e2e/build_report.py                  # no credentials: raw captures -> docs/e2e/REPORT.md and Allure results
PYTHONPATH=src:tests python -m e2e.cli list         # scenarios, layers and severity
```

`raw/` and `REPORT.md` are git-ignored.

## Interactive Allure report (not committed)

Each `build_report.py` run is kept under `build/e2e/runs/<capture time>_<passed>of<total>/` and `build/e2e/latest` points at the newest. The previous run's history is carried forward, so the trend graph grows; a scenario keeps its history while its id stays the same. Rendering needs the Allure CLI (`brew install allure`).

```bash
(cd build/e2e/latest/allure-report && python -m http.server 8766)   # Allure needs HTTP, not file://
```
