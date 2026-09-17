# ParkMind

An agentic AI concierge that plans a guest's day at a theme park, keeps the
plan valid as conditions change (wait times, ride status, weather), and
proposes explainable replans that the guest always approves before anything
changes.

Capstone project — Agentic AI Engineering (MED-42).
Team: Johanna Andrea · Sebastián Toro · Santiago Isaza.

**Architecture and decisions live in the GitHub Wiki** (Home → Architecture
→ v1/v2). This README is about running and navigating the code.

## Resources

- **Repository:** https://github.com/andrea0727/parkmind-agentic-ai
- **Project Board:** https://github.com/users/andrea0727/projects/4/views/1
- **Wiki (Source of Truth):** https://github.com/andrea0727/parkmind-agentic-ai.wiki.git

## Core principle

> The agent orchestrates. The planning engine decides. The guest model personalizes.

The LLM never picks the itinerary directly. It interprets intent and
explains results; the deterministic core in `services/planning/` scores,
optimizes, validates and replans; every plan change requires explicit guest
approval before it's ever applied.

## Project structure

```
parkmind-agentic-ai/
├── src/
│   └── parkmind/
│       ├── __init__.py          # every package below is a REGULAR package
│       │                        # with its own __init__.py — namespace
│       │                        # subpackages cost mypy/coverage/pytest
│       │                        # friction and buy nothing [P0-04]
│       │
│       ├── core/                # deterministic core — no framework imports
│       │   └── contracts/       # Pydantic domain models — data only, no logic
│       │                        # PartyConstraints, GuestProfile, Plan,
│       │                        # Event, Proposal, AccessibilityRequirements
│       │
│       ├── services/            # all business logic lives here
│       │   ├── planning/        # ParkGraph, Optimizer, ConstraintChecker,
│       │   │                    # Replanner, PlanDiff, ForecastService
│       │   ├── personalization/ # GroupPreferenceResolver, BehaviorLog,
│       │   │                    # preference_learner.py (stub, see scope.md)
│       │   ├── clients/         # ThemeParks, Open-Meteo, Postgres,
│       │   │                    # in-memory knowledge store
│       │   ├── use_cases/       # wires planning+personalization+clients
│       │   │                    # together — tools/ and graph/ call THIS,
│       │   │                    # never planning/personalization directly
│       │   └── evaluation/      # baselines + metrics (the eval harness)
│       │
│       ├── agents/               # ConciergeAgent
│       ├── graph/                 # LangGraph state + the two graphs
│       ├── tools/                  # MCP tools (data.* / knowledge.* / planner.*)
│       └── config/                  # settings.py — reads .env
│
├── tests/
│   ├── unit/                     # one file per service/model, isolated
│   └── integration/              # full graphs + the 4 agent quality checks
│
├── docs/
│   └── evaluation/               # methodology behind the metrics
│
├── notebooks/                     # exploratory only, nothing imported by the app
├── scripts/                        # seed_db.py, run_demo_scenario.py, run_baselines.py
├── database/                        # init.sql
├── ui/                                # Streamlit app
│
├── agent.py                            # root entrypoint — exposes the compiled
│                                        # graph(s) that langgraph.json points to
├── langgraph.json
├── requirements.txt         → see pyproject.toml (uses Poetry)
├── docker-compose.yml                   # Postgres only, see docs/decisions/scope.md
├── pytest.ini                            # lets pytest find `parkmind.*` without
│                                         # installing it as a package
├── .env.example
├── .gitignore
└── README.md
```

**Why this shape:** `services/` never imports from `agents/`, `graph/`,
`tools/`, or `ui/` — only the reverse. That's what lets everyone work in
parallel: once `core/contracts/` is frozen (it already is), the core team builds
`services/planning/` against real logic while the agent/personalization
track builds against those same contracts using stubs, without either side
blocking the other.

**Every folder is a regular package.** Each subfolder under `src/parkmind/`
has its own `__init__.py` — namespace packages (PEP 420) were tried and
dropped (P0-04): they cost `mypy`/`import-linter`/`pytest` friction (e.g.
`import-linter` can't resolve an implicit namespace package as a module) and
buy nothing here. `src/` still isn't pip-installed as a package; `pytest.ini`
and `agent.py` both add it to the import path, so
`from parkmind.core.contracts import Plan` works from anywhere without needing
`poetry install` to set up an editable install.

## Team ownership

| Track | Folders |
|---|---|
| Core / deterministic engine | `services/planning/`, `services/clients/` |
| Agent / personalization | `agents/`, `graph/`, `services/personalization/`, parts of `services/use_cases/` |
| Evaluation | `services/evaluation/`, `docs/evaluation/` |
| Mechanical, well-specified | `services/clients/knowledge_store.py`, `services/planning/constraint_checker.py`, `core/contracts/` |

## Getting started

```bash
# 1. clone and enter the repo, then:
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
poetry install

# 2. start Postgres
docker compose up -d

# 3. run tests (placeholders pass until real modules land)
poetry run pytest

# 4. sanity-check the graphs build
poetry run python agent.py

# 5. run the UI (once graph/ is wired up)
poetry run streamlit run ui/app.py
```

### Using Poetry

If you don't have Poetry installed:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

**Common commands:**
- `poetry install` — install all dependencies (runs automatically for new clones)
- `poetry run pytest` — run tests (also runs the import-boundary check, see below)
- `poetry run ruff check .` — lint
- `poetry run mypy src` — type check
- `PYTHONPATH=src poetry run lint-imports` — import-boundary check (`.importlinter`
  at repo root). Needs `PYTHONPATH=src` because `parkmind` isn't pip-installed as
  a package — same reason `pytest.ini` sets `pythonpath = src` for pytest; run
  bare (`poetry run lint-imports` with no `PYTHONPATH`), it fails immediately
  with "Could not find package 'parkmind'". CI sets this env var for you; a
  local shell needs it set explicitly.
- `poetry run python agent.py` — run scripts
- `poetry add <package>` — add a runtime dependency
- `poetry add --group dev <package>` — add a dev dependency
- `poetry lock` — update poetry.lock (commit this to git when dependencies change)
- `poetry update` — upgrade all dependencies to their latest versions

All four of the above run on every pull request via `.github/workflows/ci.yml` (P0-04).

## Working conventions

- **Branch per component, not per person** — `feat/planning-constraint-checker`,
  not `feat/collaborator_name-work`. Short-lived branches, PR to `main` within 2-4 days.
- **`main` is protected** — at least one review before merge.
- **Architecture decisions are frozen, not improvised.** If a PR needs to
  contradict something in the wiki's "Decisions Already Frozen" section,
  that's a team conversation first, a wiki changelog entry second, and code
  third — never the other way around.
