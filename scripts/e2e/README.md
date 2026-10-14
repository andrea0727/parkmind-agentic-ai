# E2E scripts

Thin entry points for the live E2E layer. The scenarios, judges and runtime live in `tests/e2e`; see [tests/e2e/README.md](../../tests/e2e/README.md) for credentials, setup and the Allure report.

```bash
python scripts/e2e/capture.py [ids]     # run live scenarios, write docs/e2e/raw/<id>.json (DB passwords scrubbed)
python scripts/e2e/build_report.py      # judge the captures, write docs/e2e/REPORT.md and the Allure results
```
