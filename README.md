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
│       ├── __init__.py          # the ONLY __init__.py in the whole tree —
│       │                        # every subfolder below is a namespace
│       │                        # package (PEP 420), nothing more needed
│       │
│       ├── models/              # Pydantic contracts — data only, no logic
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
├── requirements.txt
├── docker-compose.yml                   # Postgres only, see docs/decisions/scope.md
├── pytest.ini                            # lets pytest find `parkmind.*` without
│                                         # installing it as a package
├── .env.example
├── .gitignore
└── README.md
```

**Why this shape:** `services/` never imports from `agents/`, `graph/`,
`tools/`, or `ui/` — only the reverse. That's what lets everyone work in
parallel: once `models/` is frozen (it already is), the core team builds
`services/planning/` against real logic while the agent/personalization
track builds against those same contracts using stubs, without either side
blocking the other.

**One `__init__.py`, on purpose.** Python 3 doesn't require an `__init__.py`
in every folder — namespace packages (PEP 420) handle it automatically, as
long as `src/` is on the import path. `pytest.ini` and `agent.py` both add
`src/` to the path, so `from parkmind.models.plan import Plan` works from
anywhere without ceremony.

## Team ownership

| Track | Folders | Who |
|---|---|---|
| Core / deterministic engine | `services/planning/`, `services/clients/` | Core track |
| Agent / personalization | `agents/`, `graph/`, `services/personalization/`, parts of `services/use_cases/` | Team lead |
| Evaluation | `services/evaluation/`, `docs/evaluation/` | Team lead, built in parallel from week 1 |
| Mechanical, well-specified | `services/clients/knowledge_store.py`, `services/planning/constraint_checker.py`, `models/event.py` | Good starting tasks for the junior teammate |

## Getting started

```bash
# 1. clone and enter the repo, then:
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. start Postgres
docker compose up -d

# 3. run tests (placeholders pass until real modules land)
pytest

# 4. sanity-check the graphs build
python agent.py

# 5. run the UI (once graph/ is wired up)
streamlit run ui/app.py
```

## Working conventions

- **Branch per component, not per person** — `feat/planning-constraint-checker`,
  not `feat/collaborator_name-work`. Short-lived branches, PR to `main` within 2-4 days.
- **`main` is protected** — at least one review before merge. The lead
  reviews the junior teammate's PRs; peers review each other's.
- **Architecture decisions are frozen, not improvised.** If a PR needs to
  contradict something in the wiki's "Decisions Already Frozen" section,
  that's a team conversation first, a wiki changelog entry second, and code
  third — never the other way around.
