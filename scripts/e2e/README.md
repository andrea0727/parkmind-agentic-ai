# E2E on real ports

Runs the initial planning graph against **real** dependencies and records what happened per scenario (33 scenarios).

## Requires real credentials (this is not a CI test)

| What | How |
|---|---|
| NVIDIA LLM | `NVIDIA_API_KEY` and `PARKMIND_LLM_MODEL` in `.env`; `pip install langchain-nvidia-ai-endpoints` (not in `pyproject.toml`) |
| Postgres | `DATABASE_URL` in `.env`, migrated (`alembic upgrade head`); `docker compose up -d` gives you one |
| Throwaway Postgres DB with an empty catalog | `createdb parkmind_edge`, migrate it, and set `E2E_EDGE_DATABASE_URL` (default `postgresql://parkmind:parkmind@localhost:5432/parkmind_edge`). Used by `proveedor_caido_db_vacia` and `happy_catalogo_vacio`; the latter fills its catalog, so recreate the DB to re-run them |
| ThemeParks, Open-Meteo | Public internet access; no key |

Each full run makes real LLM calls (about 35, roughly 15 minutes). Results depend on the real clock: several scenarios use the park time at run time.

## Run

```bash
python scripts/e2e/capture.py                      # all 33, skipping ones already in docs/e2e/raw/
python scripts/e2e/capture.py espanol grupo_de_1   # only these
FORCE=1 python scripts/e2e/capture.py              # re-capture everything
python scripts/e2e/build_report.py                 # no credentials needed: docs/e2e/raw -> docs/e2e/REPORT.md
```

Optional env vars: `E2E_RAW_DIR`, `E2E_BAD_DATABASE_URL` (a port nobody listens on, to simulate a DB outage), `E2E_ALLURE_RESULTS`.

## Interactive Allure report (not committed)

`build_report.py` also writes Allure results to `build/e2e/allure-results` (gitignored). Needs the Allure CLI:

```bash
allure generate build/e2e/allure-results -o build/e2e/allure-report --clean
allure open build/e2e/allure-report     # Allure needs an HTTP server; index.html does not work via file://
```

## Files

- `harness.py`: env loading, NVIDIA extractor, stage spies.
- `scenarios.py`: the 33 scenarios and the outage injectors (`provider_down`, `db`).
- `capture.py`: runs scenarios, writes `docs/e2e/raw/<id>.json` (DB passwords are scrubbed).
- `build_report.py`: judges each capture against its expected behavior and renders `REPORT.md` and the Allure results.
- Docs: `docs/e2e/SCENARIOS.md` (scenario tables), `docs/e2e/REPORT.md` (latest run).
