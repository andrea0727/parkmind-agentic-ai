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
├── database/                        # alembic.ini + migrations/ (schema owner, P0-12)
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
| Mechanical, well-specified | `services/clients/knowledge/`, `services/planning/constraint_checker.py`, `core/contracts/` |

## Getting started

```bash
# 1. clone and enter the repo, then:
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
poetry install

# 2. start Postgres (built with pgvector from database/Dockerfile), then create
#    the schema (Alembic owns it) and, optionally, load the reproducible
#    development scenario
docker compose up -d --build
poetry run alembic -c database/alembic.ini upgrade head
poetry run python scripts/seed_db.py

# 3. run tests (the `db` tests need the Postgres from step 2)
poetry run pytest

# 4. sanity-check the graphs build
poetry run python agent.py

# 5. run the UI (once graph/ is wired up)
poetry run streamlit run ui/app.py
```

### Database

- **Schema:** hand-written SQL migrations in `database/migrations/versions/`,
  applied with `poetry run alembic -c database/alembic.ini upgrade head`.
  There is no `init.sql` any more — compose no longer mounts one.
- **Seed:** `poetry run python scripts/seed_db.py` loads a deterministic
  scenario (two opposing profiles, one active plan, one pending candidate). It
  is safe to re-run.
- **Reset:** `docker compose down -v && docker compose up -d`, then migrate
  again. A volume created before P0-12 still holds the old `init.sql` tables and
  must be reset this way before the first `upgrade head`.
- **pgvector (P0-26):** the compose image is `postgres:16` plus the
  `postgresql-16-pgvector` package (`database/Dockerfile`; CI installs the same
  package into its service container). Migration `0003` enables the extension.
  A container started from the old plain image stops it with a message:
  rebuild once with `docker compose up -d --build` (your data volume is kept).
- **Tests:** the repository tests are marked `db` and use throwaway
  `parkmind_test_*` databases on the same server, never your `parkmind` one.
  Without a reachable Postgres they are skipped locally and **fail** when `CI`
  is set. `PARKMIND_TEST_DATABASE_URL` points them at another server.
- **Accessibility data:** `session_only` records are held in process memory
  only and are never written to any table (see `PostgresSessionStore`).

#### Wiring the session store

There is **one** `SessionMemory` per process, `SESSION_MEMORY` in
`parkmind.services.use_cases.session_memory`. Build every store through
`session_store(conn)`, never `PostgresSessionStore(conn, SessionMemory())`: a
fresh memory would silently drop a guest's `session_only` accessibility needs
between the intake and `load_context` (a test enforces this).

```python
from parkmind.services.clients.postgres import connect
from parkmind.services.use_cases.session_memory import session_store

def handle_request(thread_id: str, guest_id: str) -> None:
    with connect() as conn:  # per request
        store = session_store(conn)
        requirements = store.get(thread_id, guest_id)  # session_id == LangGraph thread_id
        ...
```

Call `store.end_session(thread_id)` when the session ends. Persisted
(consented) records are unaffected.

**Single worker only for now.** The memory is process-local, so a deployment
with several API workers would lose records between them. P0-35 must either
fail loudly at startup with more than one worker, or use sticky sessions or a
shared non-table store.

### IDs and normalization (P0-10)

- **Internal ids.** An entity's internal id is the ThemeParks entity UUID it had
  when first seen. Every lookup goes through the `id_mapping` table
  (`services/use_cases/id_resolution.py`), so a re-issued provider id can be
  mapped back to the old internal id. Other providers (Queue-Times, later) need
  a curated mapping; they are never auto-minted.
- **Normalization** lives in `services/clients/themeparks_normalize.py`: pure
  functions over the raw payload, so a stored snapshot can be re-normalized
  without calling the API. All times become aware `America/New_York`
  datetimes (a timestamp without an offset is rejected), statuses are the
  closed `AttractionStatus` enum, and a payload in another timezone raises.
- **Problems are reported, never merged.** A duplicated provider id, missing
  curated metadata, an unknown entity type, an unmapped or conflicting id: the
  entity is left out and returned as a `MappingIssue`; the rest still loads.
  Today the 28 SHOW entities show up as `MISSING_METADATA` until they're
  curated in `themeparks_reference_data.py`.

### Snapshots (P0-11)

A snapshot is the park's live data at one moment: ThemeParks waits, statuses and
showtimes plus the hourly weather, stored as the **raw provider payloads** next
to the normalized `LiveContext` (Architecture §41, C21). Needs the schema
(`alembic ... upgrade head`, which now includes migration `0002`).

```bash
# one snapshot, then exit (exit code 0 ok, 1 database problem, 2 park data unavailable)
poetry run python scripts/collect_snapshot.py

# keep collecting every 5 minutes in the foreground (Ctrl+C to stop)
poetry run python scripts/collect_snapshot.py --loop --interval 300

# the latest valid snapshot and its age (fresh = at most 30 minutes old)
poetry run python scripts/collect_snapshot.py --latest
```

**Scheduling.** Each run is idempotent within its 5-minute window (same park,
same window → nothing written, no provider called), so a scheduler can fire it
freely:

```bash
# Windows Task Scheduler (run once, from the repo root, in cmd)
schtasks /Create /SC MINUTE /MO 5 /TN "ParkMind snapshots" ^
  /TR "cmd /c cd /d %CD% && poetry run python scripts\collect_snapshot.py >> snapshots.log 2>&1"

# cron (Linux/macOS)
*/5 * * * * cd /path/to/parkmind-agentic-ai && poetry run python scripts/collect_snapshot.py >> snapshots.log 2>&1
```

**Degrade, don't fail (§43).** Without ThemeParks live data no snapshot is
stored. If the schedule or the weather is unavailable, the snapshot is still
stored and the gap is recorded in its `coverage`. A park-wide snapshot never
claims `accessibility_checks_complete`: those checks are per party and happen
when planning loads its context.

**Re-normalization.** Every snapshot records the `NORMALIZER_VERSION`
(`services/clients/normalization.py`) that built it. When a change alters what a
raw payload normalizes to, bump that constant and rebuild the older snapshots
from their stored raw payloads — nothing is re-collected:

```bash
poetry run python scripts/renormalize_snapshots.py --dry-run   # what would be rebuilt
poetry run python scripts/renormalize_snapshots.py             # rows from an older normalizer
poetry run python scripts/renormalize_snapshots.py --all       # or every snapshot
```

### MCP capability boundary (P0-24..P0-27)

`parkmind-mcp` publishes the same use cases the graph calls in-process as typed
MCP tools (Architecture §27, C22/C23). The graph never depends on it; external
clients (the LLM-only baseline, P1-15) and, when configured, LOAD CONTEXT do.
Every result is `{data, provenance}` (§32); failures are structured
`{"error": {code, message, retryable, details}}`. No tool proposes, persists or
activates a plan.

| Namespace | Tools |
|---|---|
| `data.*` (P0-25) | `get_live_waits`, `get_attraction_status`, `get_schedule`, `get_showtimes`, `get_weather`, `get_walking_time`, `get_attraction_info` |
| `knowledge.*` (P0-26) | `search_policies`, `find_similar_attractions`, `check_accessibility` |
| `planner.*` (P0-27) | `build_plan`, `check_plan`, `score_preferences`, `forecast_waits` (`replan` follows P0-23) |

```bash
# stdio (what an MCP client launches), or streamable HTTP at http://127.0.0.1:8765/mcp
poetry run python scripts/run_mcp_server.py
poetry run python scripts/run_mcp_server.py --http

# explore it in the MCP Inspector
npx @modelcontextprotocol/inspector poetry run python scripts/run_mcp_server.py
```

**Knowledge store (P0-26).** Semantic search runs on pgvector with a local
multilingual model (fastembed, `paraphrase-multilingual-MiniLM-L12-v2`, ~220 MB,
English questions against the Spanish corpus quoted from the official park
pages). One-time setup, after `docker compose up -d --build` and
`alembic ... upgrade head`:

```bash
poetry run python scripts/fetch_embedding_model.py   # download once into ~/.cache/parkmind/fastembed
poetry run python scripts/index_knowledge.py         # embed the corpus into pgvector (idempotent)
```

Without the model or the index, `knowledge.search_policies` answers by keyword
search over the same passages and says so in `provenance.degraded` (§43);
`knowledge.check_accessibility` never degrades (fail-closed, P0-26a). PR CI never
downloads the model; the manual **Embedding smoke** workflow runs the
`requires_model` tests with it.

**LOAD CONTEXT over MCP (P0-24).** `PARKMIND_CONTEXT_TRANSPORT=mcp` makes the graph
read `data.*` and `knowledge.*` through the server at `PARKMIND_MCP_URL` (start
it with `--http`); if the server is unreachable, the in-process adapter of the
same port answers and `LoadedContext.transport` says `in_process_fallback`. The
default, `in_process`, reads the snapshot directly.

**Privacy (C19).** Planner tools take a `session_id` and `guest_ids`; the
requirements are read from the SessionStore server-side and never travel in a
request or a response (violations of the accessibility rules are reported
without their values). A server in another process sees only `persisted`
records, so a party with `session_only` requirements fails closed there.

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

## Implementation Status

| Component | Status | Details |
|---|---|---|
| **LangGraph State v2** | ✅ Done | Typed ParkMindState, state helpers, message reducer |
| **Preference Resolution** | ✅ Done | Weighted aggregation from GuestProfile (queue, walking, categories) |
| **Plan Synthesis** | ✅ Initial planning graph [P0-30] | ELICIT → CONFIRM → LOAD CONTEXT → RESOLVE GROUP → BUILD PLAN → CHECK → EXPLAIN → PROPOSE → APPROVAL. A candidate is proposed (and explained) only after it passes the ConstraintChecker; it is activated only when the human approval interrupt resumes with APPROVED. Runs on in-process use cases, no MCP server. EDITED approvals [P0-32] and replanning [P0-31] are follow-ups. |
| **MCP capability boundary** | ✅ P0-24..P0-27 | `parkmind-mcp`: 14 typed tools (`data.*`, `knowledge.*` with pgvector semantic search, `planner.*`); LOAD CONTEXT can read through it with in-process fallback. `planner.replan` follows P0-23. |
| **Weather Integration** | ✅ Done | OpenMeteo adapter (hourly forecast) |
| **Attractions Integration** | ✅ Done | ThemePark catalog adapter (rides, wait times) |
| **PostgreSQL Repos** | ✅ Done | Profiles, Plans, Session store (in-memory for accessibility) |
| **Initial Planning Graph** | ✅ Done | 5-node orchestration: resolve → fetch → synthesize → propose → approve |
| **Constraint Checker** | 🔄 Next | Validation rules, PlanDiff generation |
| **Replanner** | 🔄 Next | Proposal generation, interrupt flow |

## Working conventions

- **Branch per component, not per person** — `feat/planning-constraint-checker`,
  not `feat/collaborator_name-work`. Short-lived branches, PR to `main` within 2-4 days.
- **`main` is protected** — at least one review before merge.
- **Architecture decisions are frozen, not improvised.** If a PR needs to
  contradict something in the wiki's "Decisions Already Frozen" section,
  that's a team conversation first, a wiki changelog entry second, and code
  third — never the other way around.
