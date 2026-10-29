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

## Contents

[Prerequisites](#prerequisites) · [Getting started](#getting-started) ·
[Demo app](#demo-app-api--react-ui) · [Planning rules](#planning-rules-the-guests-will-notice) ·
[E2E](#e2e-on-real-ports-live-llm) · [Live evals](#live-conversation-evals) · [Langfuse](#observability-langfuse-local) ·
[Database](#database) · [Snapshots](#snapshots) · [Event monitor](#event-monitor) ·
[Replanning](#replanning-graph) · [Approval](#human-approval-approve--edit--reject) ·
[Explanations](#explanations) · [MCP](#mcp-capability-boundary) ·
[Configuration](#configuration-reference) · [Tests and checks](#tests-and-checks) ·
[Switching provider](#switching-the-llm-provider) · [Running every check](#running-every-check) ·
[Troubleshooting](#troubleshooting) · [Status](#implementation-status)

## Project structure

```
parkmind-agentic-ai/
├── src/
│   └── parkmind/
│       ├── __init__.py          # every package below is a REGULAR package
│       │                        # with its own __init__.py — namespace
│       │                        # subpackages cost mypy/coverage/pytest
│       │                        # friction and buy nothing
│       │
│       ├── core/                # deterministic core — no framework imports
│       │   ├── contracts/       # Pydantic domain models — data only, no logic
│       │   │                    # PartyConstraints, GuestProfile, Plan,
│       │   │                    # Event, Proposal, AccessibilityRequirements
│       │   └── language.py      # es/en message catalog (questions, notices)
│       │
│       ├── services/            # all business logic lives here
│       │   ├── planning/        # ParkGraph, Optimizer, ConstraintChecker,
│       │   │                    # Replanner, PlanDiff, ForecastService
│       │   ├── personalization/ # GroupPreferenceResolver, PreferenceScorer,
│       │   │                    # preference_learner.py (stub, see scope.md)
│       │   ├── ports/           # interfaces (repositories, park data, knowledge)
│       │   ├── clients/         # ThemeParks, Open-Meteo, Postgres, MCP client,
│       │   │                    # knowledge store (pgvector / in-memory), Langfuse
│       │   ├── use_cases/       # wires planning+personalization+clients
│       │   │                    # together — tools/ and graph/ call THIS,
│       │   │                    # never planning/personalization directly
│       │   └── evaluation/      # baselines + metrics (the eval harness)
│       │
│       ├── agents/               # elicit agent (LLM extractor, closed schema),
│       │                         # concierge, plan synthesis, preference resolver
│       ├── graph/                 # LangGraph state, the two graphs, ConversationService
│       ├── api/                   # FastAPI app: routes, auth/limits (security.py), schemas
│       ├── tools/                  # MCP tools (data.* / knowledge.* / planner.*)
│       └── config/                  # settings.py (reads .env), tracing.py
│
├── tests/
│   ├── unit/                     # one file per service/model, isolated
│   ├── integration/              # full graphs, API, MCP, Postgres, agent quality checks
│   ├── architecture/             # import boundaries and ownership rules (who may validate, who sets event policy)
│   ├── e2e/                      # layer 1: whole journeys from a shared scenario catalog, live on real ports
│   ├── workflow/                 # layer 2: the same journeys replayed offline (routing, state, interrupts)
│   ├── evals/                    # layer 3: quality of the LLM output, plus the live conversation evals
│   ├── observability/            # layer 4: traces, spans and masking reach Langfuse
│   └── resilience/, security/, performance/   # layer 5: failure injection, hostile input, latency and cost budgets
│
├── docs/
│   ├── evaluation/               # methodology behind the metrics
│   ├── testing/                  # test taxonomy and scenario matrix
│   ├── e2e/                      # live-run scenarios (SCENARIOS.md); raw/ and REPORT.md are generated
│   ├── diagrams/                 # system-architecture.html, agent-workflow.html, and draw.io files
│   │                             # (architecture overview, layers and MCP rules, testing with Docker)
│   └── context/, decisions/
│
├── .claude/
│   ├── skills/parkmind-evals/    # workflow for running and reading the live evals
│   └── agents/                   # eval-triage (classifies failures), scenario-writer (proposes new dialogues)
│
├── notebooks/                     # exploratory only, nothing imported by the app
├── scripts/                        # seed_db, collect_snapshot, renormalize_snapshots, run_monitor,
│                                   # run_mcp_server, fetch_embedding_model, index_knowledge,
│                                   # langfuse_smoke, e2e/ (live runner);
│                                   # run_demo_scenario and run_baselines are stubs
├── demo/                              # households.json: parties saved per username (demo app)
├── database/                        # alembic.ini + migrations/ (schema owner)
├── ui/
│   ├── app.py                         # Streamlit stub (not used; the demo UI is ui/web)
│   ├── prototype/                     # static HTML prototype
│   └── web/                           # React + Vite demo UI (served by server.py)
│
├── server.py                           # HTTP entrypoint: FastAPI API + built React UI on :8000
├── Dockerfile                          # builds the UI, then runs server.py
├── agent.py                            # root entrypoint — exposes the compiled
│                                        # graph(s) that langgraph.json points to
├── langgraph.json
├── pyproject.toml, poetry.lock          # dependencies (Poetry)
├── docker-compose.yml                   # Postgres (pgvector) + the demo app
├── docker-compose.langfuse.yml          # local Langfuse stack (observability)
├── pytest.ini                            # lets pytest find `parkmind.*` without
│                                         # installing it as a package
├── .env.example                         # every setting, with its default
├── .importlinter                        # import-boundary contracts
├── .github/workflows/                   # ci.yml, embedding-smoke.yml (manual)
├── .gitignore                           # build/, docs/e2e/raw/, .env are not committed
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
dropped: they cost `mypy`/`import-linter`/`pytest` friction (e.g.
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

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12+ | everything |
| [Poetry](https://python-poetry.org) | 2.x | dependencies and every `poetry run ...` command |
| Docker (with Compose) | recent | Postgres + pgvector, the demo app, Langfuse |
| Node.js | 22 (what the Dockerfile builds with) | only to run the React UI in dev mode (`ui/web`) |
| LLM API key (Anthropic by default; OpenAI or Google with `LLM_PROVIDER`) | | reading the guests' messages and writing explanations; the live e2e |
| [Allure CLI](https://allurereport.org) (`brew install allure`) | | only to render the interactive e2e report |

## Getting started

```bash
# 1. clone and enter the repo, then:
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
poetry install

# 2. start Postgres only (built with pgvector from database/Dockerfile), then create
#    the schema (Alembic owns it) and, optionally, load the reproducible
#    development scenario
docker compose up -d --build postgres
poetry run alembic -c database/alembic.ini upgrade head
poetry run python scripts/seed_db.py

# 3. run tests (the `db` tests need the Postgres from step 2)
poetry run pytest
poetry run ruff check . && poetry run mypy src && PYTHONPATH=src poetry run lint-imports

# 4. sanity-check the graphs build (prints "initial_planning: N nodes compiled", "replanning: ...")
poetry run python agent.py

# 5. run the demo app (API + React UI on :8000), see "Demo app" below.
#    Needs PARKMIND_AUTH_USERS and PARKMIND_JWT_SECRET in .env first.
docker compose up -d --build
```

### Demo app (API + React UI)

One container serves the FastAPI API and the built React UI on http://localhost:8000
(Swagger at `/docs`). It runs **one worker on purpose**: sessions live in memory.

> **Known limit (decision, not an oversight).** Conversations are checkpointed in memory
> (`default_checkpointer()`), so a restart loses open sessions and there is no horizontal
> scaling. Moving to a Postgres checkpointer needs a C19 decision first.
> `AccessibilityRequirements` are already kept out of a durable checkpoint (they live only in
> `SESSION_MEMORY` and are re-read at planning time, with a test). What is not covered is the
> guests' raw text: `state.messages` ("mi papá no puede caminar mucho") would be persisted.
> Either redact it before it is stored, or move the turns to the SessionStore, which honors
> retention.

**1. Configure `.env`** (copy from `.env.example`). Required for the app to start:

```bash
ANTHROPIC_API_KEY=sk-ant-...
# LLM_PROVIDER=openai or google (default anthropic): set OPENAI_API_KEY or GOOGLE_API_KEY instead,
# and install the package once: poetry install -E openai  (or -E google)
PARKMIND_AUTH_USERS=demo=ChangeMe1234        # name=password[,name2=password2]; 8+ chars, no commas
PARKMIND_JWT_SECRET=<32+ random chars>       # python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Optional: `PARKMIND_TOKEN_TTL_HOURS` (default 2), `PARKMIND_MESSAGES_PER_USER_DAY` (300),
`PARKMIND_MESSAGES_GLOBAL_DAY` (2000), `ELICIT_MODEL` (default per provider), `PARKMIND_AUTH_DISABLED=1` (local demo only: no login,
every route open), `PARKMIND_PLAN_FROM_STORED=1` (plan from the latest stored snapshot),
`E2E_NOW=HH:MM` (fixed park clock); both demo switches need `PARKMIND_DEMO=1` too, and the server refuses to start with one set alone. After editing `.env`: `docker compose up -d app`.

**2. Launch**

```bash
docker compose up -d --build        # Postgres + app (migrations and seed run on start)
docker compose logs -f app          # watch it start
docker compose down                 # stop (add -v to also drop the database volume)
```

Open http://localhost:8000 and sign in with the user from `PARKMIND_AUTH_USERS`.

**Without Docker** (hot reload for UI work): terminal 1 `docker compose up -d postgres`, then
`poetry run alembic -c database/alembic.ini upgrade head && poetry run python scripts/seed_db.py`
(the container does this on start because the compose file sets `PARKMIND_SEED_DEMO=1`; here you do it once) and `poetry run python server.py`; terminal 2 `cd ui/web && npm ci && npm run dev` and open
http://localhost:5173 (Vite proxies `/api` to :8000).

**3. Check that it works**

```bash
curl -s localhost:8000/api/health
# {"status":"ok","auth_required":true}

TOKEN=$(curl -s -X POST localhost:8000/api/login -H 'content-type: application/json' \
  -d '{"username":"demo","password":"ChangeMe1234"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/api/session          # 401: no token
THREAD=$(curl -s -X POST localhost:8000/api/session -H "Authorization: Bearer $TOKEN" \
  | python -c "import sys,json; print(json.load(sys.stdin)['thread_id'])")
curl -s -X POST "localhost:8000/api/message?thread_id=$THREAD" -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"text":"Somos 2 adultos y un niño de 8 años, queremos salir a las 17:00","lang":"es"}'
```

The answer is a `Turn` whose `status` says what the app waits for (`needs_answer`,
`needs_confirmation`, `needs_approval`, `needs_event_confirmation` or `done`). Then `POST /api/constraints/confirm`,
`GET /api/proposal`, `POST /api/proposal/{id}/approve|reject`, `GET /api/plan`,
`POST /api/events` and `/api/events/confirm`; all take `?thread_id=` and a Bearer token.

**Limits and errors:** `401` missing/expired token (sign in again, tokens last 2 h by default);
`404` unknown session, or one that belongs to another user; `409` the call does not fit the
session state; `429` too many requests or failed sign-ins (read `Retry-After`); `503` park data
unavailable. Caps per user: 20 messages/min and 300/day, 20 new sessions/hour; global: 2000
messages/day; 5 failed sign-ins per address+user lock that login for 5 minutes.

**Tests and checks for the app**

```bash
poetry run pytest tests/integration/test_api.py tests/integration/test_api_security.py
poetry run pytest                  # everything
poetry run ruff check . && poetry run mypy src && PYTHONPATH=src poetry run lint-imports
```

(If your shell exports `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`, unset them for the live
Anthropic tests.)

### Planning rules the guests will notice

These sit in the graph around the deterministic core; none of them lets the LLM decide
the itinerary.

- **Heights.** ELICIT asks for each guest's height only when a must-do attraction has a
  minimum height (one question for all the guests that need it). If a height is still
  unknown, the `ConstraintChecker` fails closed on that ride: the guests are never put
  on a ride whose limit they may not meet.
- **Party size.** Above `PARKMIND_MAX_PARTY_SIZE` (default 10) the guests are asked to
  split the group; no plan is built for the oversized party.
- **An adult is required.** A plan for a group with no adult cannot be approved:
  `APPROVED` is refused with an explanation, while `REJECTED` and `EDITED` stay available.
- **Shows are not stops.** Shows have fixed times, so they are not planned as stops. When
  the plan comes out empty because the guests asked for a show or the fireworks, the
  answer says so instead of staying silent.
- **Missing information is a question, never a guess.** Missing or doubtful data
  (`height_needed`, `party_too_large`, `guest_role`, `height_unverified`,
  `unknown_attraction`) is asked back in the guests' language (es/en), from the catalog in
  `core/language.py`.

### E2E on real ports (live LLM)

`scripts/e2e/` runs the initial planning graph against **real** Postgres, ThemeParks,
Open-Meteo and the chosen LLM provider (33 scenarios, ~35 LLM calls, ~15 minutes; costs API credit).
Not part of CI. Needs the key of the chosen provider (`ANTHROPIC_API_KEY` by default; see
[Switching the LLM provider](#switching-the-llm-provider)) and `DATABASE_URL` in `.env`, a migrated
database, and (for two scenarios) the throwaway `parkmind_edge` database; details in
`scripts/e2e/README.md`, scenario tables in `docs/e2e/SCENARIOS.md`.

```bash
# one-time: the throwaway database for the empty-catalog scenarios (recreate it for each full run,
# one scenario fills its catalog)
docker compose exec postgres createdb -U parkmind parkmind_edge
DATABASE_URL=postgresql://parkmind:parkmind@localhost:5432/parkmind_edge \
  poetry run alembic -c database/alembic.ini upgrade head

poetry run python scripts/e2e/capture.py                 # all scenarios, skipping the captured ones
poetry run python scripts/e2e/capture.py espanol         # only these
FORCE=1 poetry run python scripts/e2e/capture.py         # re-capture everything
poetry run python scripts/e2e/build_report.py            # docs/e2e/raw -> build/e2e/runs/<time>_<N>of33/ (no credentials)
(cd build/e2e/latest/allure-report && python -m http.server 8766)   # open http://localhost:8766
```

Captures go to `docs/e2e/raw/` and reports to `build/e2e/` (both git-ignored). Every
report run is kept with its Allure history, so the trend across runs is visible.
Optional: `E2E_NOW=HH:MM` (park clock, default 10:00), `E2E_EDGE_DATABASE_URL`,
`E2E_RAW_DIR`, `E2E_BAD_DATABASE_URL`.

### Live conversation evals

`tests/evals/` also plays scripted guest dialogues against the real models and scores each
conversation: no repeated question, no echo of the guests' words, and a naturalness score per
turn from a DeepEval decision tree. Every dialogue runs several times in two arms, the fixed
question template and the LLM question writer, and the pass rate over runs is the headline number.
Not part of CI; costs API credit. Needs the key of the chosen provider in `.env`, `ANTHROPIC_API_KEY` as well
for the Claude judge, and `poetry install --with eval`.

```bash
set -a; . ./.env; set +a
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN PYTHONPATH=src:tests .venv/bin/python -m evals.run_live --runs 5
PARKMIND_RUN_LIVE=1 poetry run pytest tests/evals -m live    # the same checks as pytest assertions
```

Results are appended to `build/evals/results.jsonl` and `build/evals/dashboard.html` is rebuilt
(pass rate per arm and dialogue, history by prompt version, every transcript with the judge's
reason per turn). With `PARKMIND_TRACING=langfuse` and the `LANGFUSE_*` variables set, each conversation is also sent to Langfuse
as a trace with the scores `conversation_passed`, `flow_ok` and `naturalness`. The judge model is
`PARKMIND_JUDGE_MODEL` (default `claude-sonnet-5`), deliberately not the model that writes the
questions. Details in `tests/evals/README.md`. For Claude Code, the `parkmind-evals` skill describes
the workflow, the `eval-triage` agent classifies each failing conversation, and `scenario-writer`
proposes harder dialogues.

**Changing the provider.** The agents (ELICIT, ASK, EXPLAIN) follow `LLM_PROVIDER`: set it, put
the matching key in `.env`, and install the extra once (`poetry install -E openai` or `-E google`);
the default models change with it unless you set `ELICIT_MODEL`, `ASK_MODEL` or `EXPLAIN_MODEL`.
The evals then judge the new provider's conversations. The judge does **not** follow
`LLM_PROVIDER`: it is Claude through `ChatAnthropic` (`tests/evals/eval_judge.py`), so
`ANTHROPIC_API_KEY` is still required to score them even when the agents run on OpenAI or Google.
Keeping one fixed judge also keeps the pass rates comparable across providers. Moving the judge
to another provider means replacing `ClaudeJudge`, not setting a variable.

### Observability: Langfuse (local)

Traces go to a **self-hosted** Langfuse (ADR 0003). Tracing is off by default and masked
when on: every input, output and metadata value passes `mask_for_trace`
(`services/clients/observability/langfuse_tracing.py`) before it leaves the process, so the
chat text (guests' and AI replies), corrections, guest labels, explanations and every
accessibility field read `[masked]` [C19]. Constraints, plans, events and ids stay readable.
A canary test (`tests/integration/test_langfuse_canary.py`) runs a real graph and checks
every exported span.

```bash
docker compose -f docker-compose.langfuse.yml up -d   # Langfuse v4 at http://localhost:3000
```

The stack creates the project `parkmind-local` and its keys on first start. Sign in with
`dev@parkmind.local` / `parkmind-local`. Then, in `.env`:

```bash
PARKMIND_TRACING=langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-parkmind-local
LANGFUSE_SECRET_KEY=sk-lf-parkmind-local
LANGFUSE_BASE_URL=http://localhost:3000
```

```bash
poetry run python scripts/langfuse_smoke.py        # one conversation, offline (fake chat model)
poetry run python scripts/langfuse_smoke.py --llm  # the same with Claude (ANTHROPIC_API_KEY)
poetry run python server.py                        # or use the app: every API turn is traced
docker compose -f docker-compose.langfuse.yml down # stop it (add -v to delete the traces)
```

What you see: one **session** per thread, a **trace** per API turn (tagged `initial` or
`replanning`), a span per graph node, and a **generation** per LLM call with its messages
reduced to their roles.

- `PARKMIND_TRACING=langfuse` with a key or the URL missing refuses to start.
- LangSmith tracing (`LANGSMITH_TRACING=true`) is still refused: its tracer is added by
  `langchain-core` and never passes through the mask.
- The stack's secrets are local defaults. Don't expose it beyond localhost, and don't send real
  guest data to a cloud project: the traces do not carry the trace metadata (plan, snapshot,
  prompt and model versions), failure correlation or the §42 scores.

### Database

- **Schema:** hand-written SQL migrations in `database/migrations/versions/`,
  applied with `poetry run alembic -c database/alembic.ini upgrade head`.
  There is no `init.sql` any more — compose no longer mounts one.
- **Seed:** `poetry run python scripts/seed_db.py` loads a deterministic
  scenario (two opposing profiles, one active plan, one pending candidate). It
  is safe to re-run.
- **Reset:** `docker compose down -v && docker compose up -d`, then migrate
  again. A volume created before the migrations still holds the old `init.sql` tables and
  must be reset this way before the first `upgrade head`.
- **pgvector:** the compose image is `postgres:16` plus the
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
        requirements = store.get(
            thread_id, guest_id
        )  # session_id == LangGraph thread_id
        ...
```

Call `store.end_session(thread_id)` when the session ends. Persisted
(consented) records are unaffected.

**Single worker only.** The memory is process-local, so a deployment with
several API workers would lose records between them. `server.py` fails loudly at
startup if more than one worker is requested (`WEB_CONCURRENCY` or
`PARKMIND_WORKERS` above 1, or `--workers`; `api/guard.py`).

### IDs and normalization

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
  The 28 SHOW entities are reported as `MISSING_METADATA`: they are not in the
  curated `themeparks_reference_data.py`, and shows are not plannable stops anyway.

### Snapshots

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

### Event monitor

The monitor compares the two most recent stored snapshots and reports what
changed in the park: an attraction going down or coming back
(`ATTRACTION_DOWN` / `ATTRACTION_AVAILABLE`), a wait spike, rain moving into
the next hours. It never wakes the LLM (Architecture §24).

```bash
poetry run python scripts/run_monitor.py                # collect one snapshot, then detect
poetry run python scripts/run_monitor.py --loop         # every 5 minutes until Ctrl+C
poetry run python scripts/run_monitor.py --no-collect   # detect on what is already stored
```

- **Stale readings never raise an alarm.** ThemeParks keeps some readings for
  months (a show closed since July, an entity last updated in 2022). A change
  only counts when the provider's own `lastUpdated` for it is within the
  30-minute freshness window; it is read from the stored raw payload (§41, C21). Unknown reading time → no event. Two
  snapshots more than 30 minutes apart are not compared (after a collector
  outage the next run only sets the baseline).
- **One policy for both sources.** `EventPolicy` (`services/planning/event_policy.py`)
  is the only code that sets `severity` and `requires_replan`, with §25's table and
  the party's own thresholds: a closure of a ride the party already did, or a spike
  below its queue tolerance, does not replan. `PARTY_RELOCATED` never does on its
  own; `ATTRACTION_AVAILABLE` only for a must-do in `unmet_must_do`.
- **One event per closure.** Ids don't depend on the source, so the monitor and a
  guest reporting the same closure record one event (`event_dedup.py`). A closure
  the monitor sees again later is a new one (the reopening was missed, or the first
  report was wrong): more than 30 minutes after a monitor event, or more than 2 h
  (`USER_EPISODE_WINDOW`, a starting point to tune with real data) after a guest's
  report, since the provider can lag behind a guest. The cost: a wrong guest report
  can hide a real closure the monitor sees within those 2 h.
  `BEHIND_SCHEDULE` is recorded once per late stop, when the delay reaches 15
  minutes, and only while the party's progress is being tracked. "Once" means once
  per stop *and planned departure*: if the guests reject or ignore the replan it
  proposes, the same stop does not raise another one, however late the party gets.
  Only an approved replan, which moves the departures, makes a new delay count as a
  new event. `BEHIND_SCHEDULE` only fires once something records progress
  (`completed_stop_ids` or `skipped_stop_ids` in `PlanExecutionState`); assumed
  progress never counts. `RecordProgressUseCase` records it, and `seed.py` writes it
  for the demo scenario; no API call reports a stop as done, so outside the
  seeded scenario the event is not raised. A replan's reported progress is carried
  over to the new plan when its proposal is approved, so what the guests report
  while deciding is not lost.
- **Confirmation first (§25, C10).** A doubtful guest report, or a fatigue report
  for a guest with a walking limit (or whose limit can't be read), is set aside
  until the guests confirm it; the rest of the batch goes on.
- **Superseding.** `HandleEventsUseCase` records a party's events and, when a new
  one requires a replan, marks the pending proposal `SUPERSEDED` (§9, C18). Approving
  a proposal superseded meanwhile activates nothing and tells the guests.
- **Next step.** The monitor prints the park-wide events; per-party judgement runs
  when `HandleEventsUseCase` is called with that party's plan and objective, which is
  what the replanning graph does (below). The monitor does not wake a party's
  graph; the replanning graph is started with the events.

### Replanning graph

`graph/replanning_graph.py` (`langgraph.json` entry `replanning`) runs on the thread
of the initial plan, with `{"thread_id": ..., "events": [Event, ...]}` as input;
constraints, guest profiles, `accessibility_ref` and the group objective come from
that thread's checkpoint (so `agent.py` gives both graphs one checkpointer).

```
LOAD PLAN → HANDLE EVENTS (dedup · apply execution updates · EventPolicy)
   ├─ nothing significant → IGNORE (a pending proposal is put back in front of the guests)
   ├─ report to confirm   → CONFIRM EVENTS (interrupt) → HANDLE CONFIRMED EVENTS
   └─ significant         → LOAD CONTEXT → RESOLVE GROUP → REPLAN → CHECK
                              ├─ valid   → DIFF → EXPLAIN → PROPOSE → APPROVAL (interrupt)
                              └─ invalid → END: nothing proposed, the active plan stays
```

- **Same gate as the initial plan.** A replan is a new `plan_id`, proposed only after
  `ConstraintChecker.check_replan`, which judges the *whole day*: what the party
  already walked counts against the walking limits, and a stop already completed is
  not held against a closure that came later.
- **Human decides.** Only the resumed approval interrupt activates a plan
  (`ResolveProposalUseCase`); rejecting leaves the active plan untouched. A new
  significant event supersedes the proposal that was waiting (`supersede_pending`).
- **Progress.** The replan starts from the stored `PlanExecutionState`. Reported stops
  (completed or skipped) are taken as reported; every other stop that should have
  ended by now is assumed done, as for an on-schedule party, so a partial report never
  puts the past back in the plan. A stop that wasn't done must be reported skipped.
  A closure or reopening reported after the snapshot is applied
  to the replan's context so the replan and its check see the same availability.
- **Fatigue.** A confirmed `GUEST_FATIGUE` sets the walking cap and makes the
  new horizon open with a `REST` stop (§25). If a rest cannot fit, the replan fails
  closed.
- **Walking budget.** The optimizer is given what is left of the party budget, the
  daily limits and the fatigue caps, so it stops adding attractions at the limit
  instead of leaving the checker to reject the plan. This also applies to initial
  plans (`walking_done_minutes` is zero there).
- **Privacy [C19].** State holds only `accessibility_ref`; every use case reads the
  requirements from the SessionStore.
- **Known limits.** After a replan finds no valid plan the events stay recorded, so a
  duplicate report does not retry it (a new event does). REJECT's "update
  preferences" step is not implemented (the reason is stored on the proposal and in
  the behavior log). An in-progress stop is treated as still ahead.

### Human approval: approve / edit / reject

Both planning graphs pause at the same approval interrupt
(`"kind": "plan_approval"`). Resume it with one of:

```python
{"decision": "APPROVED"}
{"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"}   # reason optional (OTHER)
{"decision": "EDITED", "edit": {"remove": ["<stop node id>"], "add": ["<attraction node id>"]}}
```

The reason is read only with `REJECTED`, and an `edit` is only accepted with
`EDITED`. Resume by interrupt id (`Command(resume={interrupt.id: value})`): if a run
fails after the answer was taken (missing park data, say), LangGraph keeps the
answer and `invoke(None, config)` retries with it. Inside the planning subgraph, a
plain resume value kept that way would also answer the *next* interrupt.

- **Approve** activates the candidate through `PlanRepository.activate`, which
  atomically requires an `APPROVED` proposal. **Reject** stores the reason category
  and keeps the current plan (if any).
- **Edit is never a plan.** The guests say which stops to drop and which
  attractions to add, nothing else (no times or waits). `ApplyEditUseCase`
  turns that into the party's constraints, with the dropped stops avoided and the
  added attractions as must-dos (`constraints_version` + 1). The edited proposal is
  marked `EDITED` and never activates anything. Planning then starts again at LOAD
  CONTEXT, so the new candidate is built (or replanned) from fresh data, checked,
  explained and proposed as a new `PENDING` proposal. An edit that does not fit the
  plan (unknown id, a meal stop, a stop already done…) is answered with the same
  interrupt plus an `"error"`, and nothing changes.
- **An edit that cannot be planned gives back what the guests had.** If no valid
  plan fits the edit (for example an open attraction added when the day has no
  room left), the guests are told why, and the plan before the edit, with the
  constraints before it, is checked again on the fresh data and proposed anew, under
  a new plan id (it is explained again, and a stored plan body never changes). If it
  no longer passes either, nothing is proposed, as after any failed check. An
  attraction that is down is not a failed edit: it is planned as an unmet must-do
  ("could not fit today").
- **Known limit: missing park data while an edit is re-planned.** The run ends with
  the usual "planning unavailable" message and nothing is re-offered: the edited
  proposal is already `EDITED`, and the constraints keep the edit, so the next
  attempt plans with it.
- **Behavior log.** Every decision writes one `BehaviorEntry` per guest, in the
  same transaction (§23). The log is keyed by guest, and `g1`, `g2` are only unique
  within a party, so each guest is registered as `"{thread_id}:{guest_id}"`, with
  its role only (no height). A superseded proposal logs nothing, and a decision
  applied twice is logged once (`entry_id` is the proposal and the decision). A
  decision without the party's guests is refused. An edit also records what it
  removed apart from what it added (see below).

### Behavior log and progress

The `BehaviorLog` is its own aggregate, keyed by guest (§33 [C12]); it never lives
inside a profile.

- **Stable types.** `BehaviorEventType`: `PROPOSAL_ACCEPTED`, `PROPOSAL_REJECTED`,
  `PROPOSAL_EDITED`, `ATTRACTION_COMPLETED`, `ATTRACTION_SKIPPED` and
  `ALTERNATIVE_REQUESTED`. The last one has no producer: no flow defines what
  asking for an alternative is.
- **Context.** Each entry names its plan, and its proposal for a decision.
  `attraction_ids` are the attractions the entry is about: the plan's for a
  decision, the stop's for progress.
- **Edits.** A `PROPOSAL_EDITED` entry also carries `removed_attraction_ids` and
  `added_attraction_ids`. They are kept apart because they are opposite signals: a
  removal is a rejection, an addition is interest. Only an edit may carry them (a
  contract validator). Older entries have neither field and still read: for such
  an entry the direction is **unknown**, not "removed nothing" (its
  `attraction_ids` mixed both).
- **Progress.** `RecordProgressUseCase` records that the party completed or skipped
  a stop of its **active** plan. In one transaction it adds the stop to the stored
  `PlanExecutionState` (what the replanner keeps and `BEHIND_SCHEDULE` reads) and,
  for an attraction or show, writes `ATTRACTION_COMPLETED` / `ATTRACTION_SKIPPED`
  for each guest the stop served. Meal and rest stops change the progress only.
  - The same report twice writes nothing the second time (`entry_id` is plan, stop
    and outcome). A stop can't be both completed and skipped, and a late report
    never moves the progress back in time.
  - The party must be the plan's (`per_guest_satisfaction` and the stops'
    `served_guests`): a missing or unknown guest refuses the report, so behavior is
    never logged for an invented party. If the group changes during the day, the
    plan no longer matches it: reports are refused with `PARTY_MISMATCH` until the
    party is replanned, not recorded against the old group.
  - Every refusal is an `InvalidProgressError` with a `ProgressProblem` (no guests, a
    time without a timezone, not the active plan, not a stop, party mismatch, the
    other outcome already recorded), so an API can map them to one response.
  - Writers of a plan's progress (this use case, `HandleEventsUseCase`, and the
    approval of a replan, which copies the base plan's progress to the new plan)
    hold the plan's lock (`ExecutionStateRepository.lock`, a row lock in Postgres)
    for the read-modify-write, so two reports, a report and an event, or a report
    and an approval never overwrite each other. A report takes the lock before it
    checks the plan is the active one, so one that waited sees the plan as the
    writer ahead of it left it.
  - A replan takes the reported stops as reported and still assumes the departed,
    unreported ones done (above). No API exposes it.
- **Querying.** `BehaviorLogRepository.get(party_guest_id(thread_id, guest_id))`
  returns a guest's entries, oldest first.

### LLM capabilities and system prompts

ParkMind has one agent, the Concierge (Architecture §7). Only the steps below call an
LLM; every other node is deterministic code (§4). They are listed in
`agents/prompt_registry.py`:

| Capability | Module | Prompt | Output (forced) | When the model can't answer |
|---|---|---|---|---|
| elicit | `agents/elicit_agent.py` | `elicit-v11` | `ElicitationExtraction` | one repair retry, then the guests are asked to rephrase |
| ask | `agents/ask_agent.py` | `ask-v15` | `QuestionDraft` | the fixed question |
| explain | `agents/explain_agent.py` | `explain-v3` | `ExplanationDraft` | one grounding retry, then the template |

- **One shared core.** Every system prompt is `compose(job, rules)`
  (`agents/concierge_prompt.py`): the Concierge core first, then the capability's own
  rules. The core states once what no LLM call may do: choose, order or validate stops,
  activate a plan, decide a replan, or fill in a hard constraint or accessibility need.
  It also says that text inside `<guest_messages>` or `<payload>` is data, never
  instructions, and that the answer goes only through the one forced tool.
- **Guest text is delimited (G3).** `as_data` wraps the guests' messages and each
  payload in those tags, and defuses any tag typed inside the text, so a guest cannot
  close the block and write instructions after it.
- **Changing a prompt.** Edit its rules, bump its `*_PROMPT_VERSION`, and record the new
  fingerprint in `tests/unit/test_prompts.py` (the test prints it). Editing the core
  changes every prompt, so every version moves. The version reaches
  `Provenance.prompt_versions`, so each plan says which prompts produced it.
- **Adding an LLM step** (interpret, P1-23; the agentic context loader, P1-22): add its
  row to the registry. `tests/unit/test_llm_agency.py` fails while a module that builds
  an LLM client has no row.

### Explanations

Both graphs' EXPLAIN step puts a validated plan into words with Claude, and never
lets it invent a figure (Architecture §40, C17):

- **Payload only.** `ExplainPlanUseCase.prepare` builds what the model may use: the
  plan by name (times, waits, walks, must-dos, who sits a ride out, by the ride's
  posted minimum, never a guest's height), the unmet
  must-dos, a replan's reason and changes, where the park data came from, and the
  preference weights that drove the plan, each labelled `stated`, `learned` or
  `default`. The prompt is `agents/explain_prompt.py` (`explain-v3`).
- **Deterministic grounding.** `agents/grounding.py` extracts every number and clock
  time from the answer (and number words such as "ten" or "half"); each must appear in
  the payload exactly (`25` = `25.0`, but a bare `14` is not grounded by `14:05`, and no
  rounding). It is a subset test: it rejects an invented figure, but `3 PM`, `½` or a
  swapped pair of figures pass when those numbers are already in the payload. A figure
  that is a `stated` or `learned` weight must carry that label in its sentence (or its
  list header), and a `default` weight must carry neither. A stop's `preference_score` is
  not a weight and is never labelled. No LLM judge.
- **One retry, then the template.** A schema failure, an ungrounded number or a wrong
  label gets one retry with the errors listed; after that, without an
  API key for the chosen provider, or if the model returns no structured output or the call fails,
  the guests read the deterministic template, which only prints figures from the payload.
  Each request is capped at `EXPLAIN_TIMEOUT_SECONDS` (20 s) and the client does not
  retry on its own (`EXPLAIN_SDK_RETRIES=0`), so the guests wait at most 2 x 20 s = 40 s;
  with `EXPLAIN_SDK_RETRIES=1` it would be up to 80 s. The proposal's provenance records
  which one wrote it (`prompt_versions["explain"]`: `explain-v3` or `template`).
- **English narrator only.** The narrator writes English; with `lang` other than `en` (for
  example Spanish) the guests read the localized deterministic template, so the same
  grounding guarantees hold but the wording is fixed. The narrator payload names a guest
  who sits a ride out as `Guest N`, never by their free-text label ("tu mamá"); only the
  template, which stays in the process, uses the label.
- **Settings:** `EXPLAIN_MODEL` (defaults to `ELICIT_MODEL`), `EXPLAIN_TIMEOUT_SECONDS`,
  `EXPLAIN_SDK_RETRIES`. Tests inject a fake
  narrator (`tests/explain_support.py`), so they never call Claude, even with a key in
  `.env`.

### MCP capability boundary

`parkmind-mcp` publishes the same use cases the graph calls in-process as typed
MCP tools (Architecture §27, C22/C23). The graph never depends on it; external
clients (the LLM-only baseline) and, when configured, LOAD CONTEXT do.
Every result is `{data, provenance}` (§32); failures are structured
`{"error": {code, message, retryable, details}}`. No tool proposes, persists or
activates a plan.

| Namespace | Tools |
|---|---|
| `data.*` | `get_live_waits`, `get_attraction_status`, `get_schedule`, `get_showtimes`, `get_weather`, `get_walking_time`, `get_attraction_info` |
| `knowledge.*` | `search_policies`, `find_similar_attractions`, `check_accessibility` |
| `planner.*` | `build_plan`, `check_plan`, `replan`, `score_preferences`, `forecast_waits` |

```bash
# stdio (what an MCP client launches), or streamable HTTP at http://127.0.0.1:8765/mcp
poetry run python scripts/run_mcp_server.py
poetry run python scripts/run_mcp_server.py --http

# explore it in the MCP Inspector
npx @modelcontextprotocol/inspector poetry run python scripts/run_mcp_server.py
```

**Trust model.** The server has no authentication, so it serves only this
machine: stdio answers the process that launched it, and HTTP binds to loopback
(any other `--host` is refused; the SDK also checks Host and Origin against DNS
rebinding). A `session_id` works as a capability: whoever reaches the server and
knows one gets plans that reflect that session's accessibility requirements, and
violations name the rule and the stop (the planner tools' diagnostics) though never the
requirement. That is the demo's single-machine boundary; serving it on a network
needs authentication first (with the API layer).

**Knowledge store.** Semantic search runs on pgvector with a local
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
`knowledge.check_accessibility` never degrades (fail-closed). PR CI never
downloads the model; the manual **Embedding smoke** workflow runs the
`requires_model` tests with it.

**LOAD CONTEXT over MCP.** `PARKMIND_CONTEXT_TRANSPORT=mcp` makes the graph
read `data.*` and `knowledge.*` through the server at `PARKMIND_MCP_URL` (start
it with `--http`); if the server is unreachable, the in-process adapter of the
same port answers and `LoadedContext.transport` says `in_process_fallback`. The
default, `in_process`, reads the snapshot directly.

**Privacy (C19).** No tool takes `AccessibilityRequirements`. Planner tools take
a `session_id` and `guest_ids`; the requirements are read from the SessionStore
server-side and never travel in a request or a response (violations of the
accessibility rules are reported without their values). A server in another
process sees only `persisted` records, so a party with `session_only`
requirements fails closed there. `knowledge.check_accessibility` takes derived
flags, as §30 defines it, so external clients such as the LLM-only baseline can
check eligibility without a session; LOAD CONTEXT sends the flags without saying
whose they are, and its transport errors never quote a request.

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

`ruff`, `mypy`, `lint-imports` and `pytest` run on every pull request via
`.github/workflows/ci.yml` (Python 3.12, Postgres 16 with pgvector as a service).

## Configuration reference

Everything is read from `.env` (copy `.env.example`; it lists every setting with its
default). The ones you will touch:

| Variable | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | LLM behind ELICIT, ASK and EXPLAIN: `anthropic`, `openai` or `google`. The last two are optional installs (`poetry install -E openai` / `-E google`) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | – | Key of the chosen provider; reads the guests' messages (ELICIT), words the questions (ASK) and writes explanations (EXPLAIN) |
| `ELICIT_MODEL` | `claude-haiku-4-5-20251001`, `gpt-4o-mini` or `gemini-2.5-flash` | Model for elicitation (default depends on the provider) |
| `EXPLAIN_MODEL` | `ELICIT_MODEL` | Model for explanations |
| `EXPLAIN_TIMEOUT_SECONDS` / `EXPLAIN_SDK_RETRIES` | `20` / `0` | Explain request cap and client retries |
| `ASK_MODEL` | `ELICIT_MODEL` | Model that words the questions to the guests |
| `ASK_TIMEOUT_SECONDS` | `10` | Ask request cap; past it the fixed question is used |
| `PARKMIND_LLM_CALLS_PER_THREAD` | `40` | LLM calls the extractor and narrator of one conversation may make; past it the agents use their deterministic path |
| `PARKMIND_ASK_CALLS_PER_THREAD` | `20` | LLM calls the question writer (ASK) of one conversation may make, counted apart so it cannot use up the calls above; past it the fixed question is used |
| `PARKMIND_MAX_PARTY_SIZE` | `10` | Largest party the planner accepts |
| `DATABASE_URL` | `postgresql://parkmind:parkmind@localhost:5432/parkmind` | Postgres |
| `THEMEPARKS_BASE_URL`, `OPEN_METEO_BASE_URL` | public APIs | Park and weather data (no key) |
| `PARKMIND_AUTH_USERS`, `PARKMIND_JWT_SECRET` | – (required) | API sign-in; the server refuses to start without them |
| `PARKMIND_TOKEN_TTL_HOURS` | `2` | Token lifetime |
| `PARKMIND_MESSAGES_PER_USER_DAY` / `PARKMIND_MESSAGES_GLOBAL_DAY` | `300` / `2000` | LLM usage caps |
| `PARKMIND_AUTH_DISABLED` | off | Local demo only: every route open |
| `PARKMIND_DEMO`, `PARKMIND_PLAN_FROM_STORED`, `E2E_NOW` | off | Demo switches (the last two need `PARKMIND_DEMO=1`) |
| `PARKMIND_SEED_DEMO` | off (`1` in compose) | Load the dev scenario on container start |
| `PORT`, `PARKMIND_UI_DIR`, `PARKMIND_CORS_ORIGINS` | `8000`, `ui/web/dist`, – | `server.py` port, built UI folder, extra CORS origins |
| `PARKMIND_HOUSEHOLDS_FILE` | `demo/households.json` | Parties saved per username |
| `PARKMIND_CONTEXT_TRANSPORT`, `PARKMIND_MCP_URL` | `in_process`, `http://127.0.0.1:8765/mcp` | LOAD CONTEXT transport |
| `PARKMIND_MCP_TOKEN`, `PARKMIND_MCP_PLANNER_CALLS_PER_MINUTE` | –, `30` | MCP HTTP bearer token and `planner.*` rate limit |
| `PARKMIND_KNOWLEDGE_BACKEND` | `pgvector` (`in_memory` in compose) | Semantic or keyword knowledge search |
| `PARKMIND_EMBEDDING_MODEL`, `PARKMIND_EMBEDDING_CACHE` | MiniLM multilingual, `~/.cache/parkmind/fastembed` | Local embedding model |
| `PARKMIND_TRACING`, `LANGFUSE_*`, `PARKMIND_TRACING_ENVIRONMENT` | `off` | Masked Langfuse tracing |
| `PARKMIND_TEST_DATABASE_URL` | – | Postgres server for the `db` tests |
| `PARKMIND_RUN_LIVE` | off | `1` runs the tests marked `live` (same as `pytest --live`) |
| `PARKMIND_JUDGE_MODEL` | `claude-sonnet-5` | Model that judges the evals |
| `PARKMIND_EVAL_RUNS` / `PARKMIND_CONVERSATION_RUNS` | `3` / `5` | Repetitions of the live consistency check / live conversations per dialogue and arm |
| `PARKMIND_WORKERS` / `WEB_CONCURRENCY` | `1` | Must stay at 1 (sessions live in memory) |

## Tests and checks

```bash
poetry run pytest                                   # everything (db tests need the compose Postgres)
poetry run pytest tests/unit                        # fast, no database
poetry run pytest -m "not db"                       # skip everything that needs Postgres
poetry run pytest tests/integration/test_initial_planning_graph.py -k adult        # a single test by keyword
poetry run pytest --cov=src/parkmind                # coverage
poetry run ruff check .                             # lint
poetry run mypy src                                 # types
PYTHONPATH=src poetry run lint-imports              # import boundaries (.importlinter)
```

Markers (`pytest.ini`): `db` needs a real Postgres (skipped locally when unreachable,
**fails** when `CI` is set); `requires_model` needs the local embedding model (skipped when
it is not cached; the manual **Embedding smoke** workflow runs it); `live` calls real services
and is skipped unless `--live` or `PARKMIND_RUN_LIVE=1`. Every test also gets a layer marker from
its folder (`e2e`, `workflow`, `llm_eval`, `observability`, `reliability`, `security`,
`performance`), so `pytest -m workflow` or `pytest -m "not live"` work. Most tests use a fake LLM;
the live ones need the key of the chosen provider (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`GOOGLE_API_KEY`). The full live checks are the e2e and the live conversation evals above;
the taxonomy is in `docs/testing/README.md`.

### Switching the LLM provider

The agents (ELICIT, ASK, EXPLAIN) run on `anthropic` (default), `openai` or `google`. To switch:

```bash
poetry install -E openai            # or -E google; anthropic needs no extra
# .env
LLM_PROVIDER=openai                 # or google
OPENAI_API_KEY=sk-...               # or GOOGLE_API_KEY
# optional: ELICIT_MODEL / ASK_MODEL / EXPLAIN_MODEL (defaults: gpt-4o-mini, gemini-2.5-flash)
```

Nothing else changes: the demo app, the e2e and the evals read the same settings. Keep
`ANTHROPIC_API_KEY` too if you run the evals, because the judge is always Claude.

The Docker image installs only the Anthropic package. To run the container on another
provider, set `LLM_PROVIDER`, its key and `LLM_EXTRAS=openai` (or `google`) in `.env` and
rebuild: `docker compose up -d --build`. Without the extra the server refuses to start and
says which package is missing.

### Running every check

| What | Command | Needs |
|---|---|---|
| Offline suite (unit, integration, workflow, evals, observability, resilience, security, performance) | `poetry run pytest -m "not live and not db"` | nothing |
| Plus the Postgres tests | `poetry run pytest` (after `docker compose up -d`) | Postgres |
| Lint, types, import boundaries | `poetry run ruff check . && poetry run mypy src && PYTHONPATH=src poetry run lint-imports` | nothing |
| One layer | `poetry run pytest -m workflow` (or `e2e`, `llm_eval`, `observability`, `reliability`, `security`, `performance`) | nothing for the offline ones |
| E2E on real ports | `poetry run python scripts/e2e/capture.py`, then `build_report.py` ([E2E](#e2e-on-real-ports-live-llm)) | provider key, Postgres, `parkmind_edge` |
| Live tests through pytest | `PARKMIND_RUN_LIVE=1 poetry run pytest -m live` | provider key, Postgres, and `ANTHROPIC_API_KEY` for the judge |
| Live conversation evals and dashboard | `python -m evals.run_live --runs 5` ([Live evals](#live-conversation-evals)) | provider key, `ANTHROPIC_API_KEY` for the judge, `poetry install --with eval` |
| Langfuse traces | `docker compose -f docker-compose.langfuse.yml up -d` ([Langfuse](#observability-langfuse-local)) | Docker |

If your shell exports `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`, unset them before any live run.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `401` on every Anthropic call | Your shell exports `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` (a proxy). Unset them and use the key from `.env`. |
| Server exits at start | `PARKMIND_AUTH_USERS` / `PARKMIND_JWT_SECRET` missing; a demo switch set without `PARKMIND_DEMO=1`; more than one worker; or LangSmith tracing on. The message names which. |
| `Could not find package 'parkmind'` from `lint-imports` | Run it with `PYTHONPATH=src`. |
| `db` tests skipped | Postgres is not running: `docker compose up -d postgres`, then migrate. |
| Old tables / `init.sql` leftovers, migration fails | Volume from before the migrations: `docker compose down -v`, then migrate again. |
| The Postgres container stops with a pgvector message | Plain image from before: `docker compose up -d --build`. |
| Port 5432 or 8000 already in use | Stop the other service, or change the host port in `docker-compose.yml`. |
| `knowledge.search_policies` says `degraded` | Model or index missing: run `fetch_embedding_model.py` and `index_knowledge.py`. |
| The Allure report is blank | Serve it over HTTP (`python -m http.server`); `index.html` does not work from `file://`. |
| Replies are in the template wording | No key for the chosen provider, the per-thread call cap was reached, or the model output failed grounding twice. |

## Implementation Status

| Component | Status | Details |
|---|---|---|
| **LangGraph State v2** | ✅ Done | Typed ParkMindState, state helpers, message reducer |
| **Preference Resolution** | ✅ Done | Weighted aggregation from GuestProfile (queue, walking, categories) |
| **Plan Synthesis** | ✅ Initial planning graph | ELICIT (asks heights when a must-do needs one, caps the party size) → CONFIRM → LOAD CONTEXT → RESOLVE GROUP → BUILD PLAN → CHECK → EXPLAIN → PROPOSE → APPROVAL. A candidate is proposed (and explained) only after it passes the ConstraintChecker; it is activated only when the human approval interrupt resumes with APPROVED, and never for a group without an adult. Runs on in-process use cases, no MCP server. APPROVE / EDIT / REJECT: an edit is re-planned, re-checked and proposed again. |
| **Explanations** | ✅ Done | Claude explains the validated plan from a payload only (plan, diff, provenance, stated/learned preference weights); every numeral is checked against the payload, one retry, then the deterministic template. |
| **Event monitor** | ✅ Done | Snapshot-to-event detector with per-reading freshness, `EventPolicy` (§25 table, confirmation gate, fatigue walking cap), cross-source dedup, supersede on significant events, `scripts/run_monitor.py`. Consumed by the replanning graph. |
| **MCP capability boundary** | ✅ Done | `parkmind-mcp`: 15 typed tools (`data.*`, `knowledge.*` with pgvector semantic search, `planner.*`); LOAD CONTEXT can read through it with in-process fallback. `planner.replan` takes the plan, its progress and the events inline (§31), so a client without a stored plan can replan. |
| **Demo app (API + UI)** | ✅ Done | FastAPI layer over the graphs (`/api/*`, Swagger at `/docs`) and a React UI; bearer-token login, per-user session ownership, LLM usage caps, es/en conversation. |
| **Observability** | ✅ Done | Masked Langfuse tracing of every API graph run, opt-in (`PARKMIND_TRACING=langfuse`); local stack in `docker-compose.langfuse.yml`; C19 canary over exported spans. |
| **Weather Integration** | ✅ Done | OpenMeteo adapter (hourly forecast) |
| **Attractions Integration** | ✅ Done | ThemePark catalog adapter (rides, wait times) |
| **PostgreSQL Repos** | ✅ Done | Profiles, Plans, Session store (in-memory for accessibility) |
| **Constraint Checker** | ✅ Done | 11 rules, plus `check_replan` for a replanned day; PlanDiff generation |
| **Replanner** | ✅ Done | Replanner (frozen completed stops, remaining walking budget, rest-first) and the replanning graph: events → policy → replan → check → diff → explain → propose → approval. |

## Working conventions

- **Branch per component, not per person** — `feat/planning-constraint-checker`,
  not `feat/collaborator_name-work`. Short-lived branches, PR to `main` within 2-4 days.
- **`main` is protected** — at least one review before merge.
- **Architecture decisions are frozen, not improvised.** If a PR needs to
  contradict something in the wiki's "Decisions Already Frozen" section,
  that's a team conversation first, a wiki changelog entry second, and code
  third — never the other way around.
