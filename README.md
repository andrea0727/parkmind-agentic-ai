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
│       ├── agents/               # elicit agent (Anthropic extractor, closed schema),
│       │                         # concierge, plan synthesis, preference resolver
│       ├── graph/                 # LangGraph state, the two graphs, ConversationService
│       ├── api/                   # FastAPI app: routes, auth/limits (security.py), schemas
│       ├── tools/                  # MCP tools (data.* / knowledge.* / planner.*)
│       └── config/                  # settings.py — reads .env
│
├── tests/
│   ├── unit/                     # one file per service/model, isolated
│   └── integration/              # full graphs + the 4 agent quality checks
│
├── docs/
│   ├── evaluation/               # methodology behind the metrics
│   ├── e2e/                      # live-run scenarios (SCENARIOS.md) and last report
│   ├── diagrams/                 # architecture.html
│   └── context/, decisions/
│
├── notebooks/                     # exploratory only, nothing imported by the app
├── scripts/                        # seed_db, collect_snapshot, renormalize_snapshots, run_monitor,
│                                   # run_mcp_server, fetch_embedding_model, index_knowledge,
│                                   # e2e/ (live runner); run_demo_scenario and run_baselines are stubs
├── database/                        # alembic.ini + migrations/ (schema owner, P0-12)
├── ui/
│   ├── app.py                         # Streamlit stub (not used; the demo UI is ui/web)
│   └── web/                           # React + Vite demo UI (served by server.py)
│
├── server.py                           # HTTP entrypoint: FastAPI API + built React UI on :8000
├── Dockerfile                          # builds the UI, then runs server.py
├── agent.py                            # root entrypoint — exposes the compiled
│                                        # graph(s) that langgraph.json points to
├── langgraph.json
├── pyproject.toml, poetry.lock          # dependencies (Poetry)
├── docker-compose.yml                   # Postgres + the demo app
├── pytest.ini                            # lets pytest find `parkmind.*` without
│                                         # installing it as a package
├── .env.example
├── .github/workflows/                   # ci.yml, embedding-smoke.yml
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

# 2. start Postgres only (built with pgvector from database/Dockerfile), then create
#    the schema (Alembic owns it) and, optionally, load the reproducible
#    development scenario
docker compose up -d --build postgres
poetry run alembic -c database/alembic.ini upgrade head
poetry run python scripts/seed_db.py

# 3. run tests (the `db` tests need the Postgres from step 2)
poetry run pytest

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
> scaling. A Postgres checkpointer is a follow-up, and it needs a C19 decision first.
> `AccessibilityRequirements` are already kept out of a durable checkpoint (they live only in
> `SESSION_MEMORY` and are re-read at planning time, with a test). What is not covered is the
> guests' raw text: `state.messages` ("mi papá no puede caminar mucho") would be persisted.
> Either redact it before it is stored, or move the turns to the SessionStore, which honors
> retention.

**1. Configure `.env`** (copy from `.env.example`). Required for the app to start:

```bash
ANTHROPIC_API_KEY=sk-ant-...
PARKMIND_AUTH_USERS=demo=ChangeMe1234        # name=password[,name2=password2]; 8+ chars, no commas
PARKMIND_JWT_SECRET=<32+ random chars>       # python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Optional: `PARKMIND_TOKEN_TTL_HOURS` (default 2), `PARKMIND_MESSAGES_PER_USER_DAY` (300),
`PARKMIND_MESSAGES_GLOBAL_DAY` (2000), `ELICIT_MODEL` (Anthropic model, default `claude-haiku-4-5-20251001`), `PARKMIND_AUTH_DISABLED=1` (local demo only: no login,
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

### E2E on real ports (live LLM)

`scripts/e2e/` runs the initial planning graph against **real** Postgres, ThemeParks,
Open-Meteo and Anthropic (33 scenarios, ~35 LLM calls, ~15 minutes; costs API credit).
Not part of CI. Needs `ANTHROPIC_API_KEY` and `DATABASE_URL` in `.env`, a migrated
database, and (for two scenarios) the throwaway `parkmind_edge` database; details in
`scripts/e2e/README.md`.

```bash
poetry run python scripts/e2e/capture.py                 # all scenarios, skipping the captured ones
poetry run python scripts/e2e/capture.py espanol         # only these
FORCE=1 poetry run python scripts/e2e/capture.py         # re-capture everything
poetry run python scripts/e2e/build_report.py            # docs/e2e/raw -> docs/e2e/REPORT.md (no credentials)
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

### Event monitor (P0-33)

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
  progress never counts. Today only `seed.py` writes it, so in production the event
  never fires until reporting a stop as done is built (#48, P0-35b): that is an
  acceptance criterion to carry into that ticket. A replan's reported progress is
  carried over to the new plan when its proposal is approved, so what the guests
  report while deciding is not lost.
- **Confirmation first (§25, C10).** A doubtful guest report, or a fatigue report
  for a guest with a walking limit (or whose limit can't be read), is set aside
  until the guests confirm it; the rest of the batch goes on.
- **Superseding.** `HandleEventsUseCase` records a party's events and, when a new
  one requires a replan, marks the pending proposal `SUPERSEDED` (§9, C18). Approving
  a proposal superseded meanwhile activates nothing and tells the guests.
- **Next step.** The monitor prints the park-wide events; per-party judgement runs
  when `HandleEventsUseCase` is called with that party's plan and objective, which is
  what the replanning graph does (below). Waking a party's graph from the monitor
  is not wired yet.

### Replanning graph (P0-34)

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
- **Progress.** The replan starts from the stored `PlanExecutionState`. If nobody has
  reported progress, the party is assumed on schedule: stops that should have ended
  by now count as done. A closure or reopening reported after the snapshot is applied
  to the replan's context so the replan and its check see the same availability.
- **Fatigue.** A confirmed `GUEST_FATIGUE` sets the walking cap (P0-33) and makes the
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
  the behavior log; P1-03). An in-progress stop is treated as still ahead.

### Human approval: approve / edit / reject (P0-32)

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
  decision without the party's guests is refused. An edit's `attraction_ids` mixes
  what was removed and what was added; the direction is a P1-01 follow-up (#51).

### Explanations (P0-31)

Both graphs' EXPLAIN step puts a validated plan into words with Claude, and never
lets it invent a figure (Architecture §40, C17):

- **Payload only.** `ExplainPlanUseCase.prepare` builds what the model may use: the
  plan by name (times, waits, walks, must-dos, who sits a ride out, by the ride's
  posted minimum, never a guest's height), the unmet
  must-dos, a replan's reason and changes, where the park data came from, and the
  preference weights that drove the plan, each labelled `stated`, `learned` or
  `default`. The prompt is `agents/explain_prompt.py` (`explain-v1`).
- **Deterministic grounding.** `agents/grounding.py` extracts every number and clock
  time from the answer; each must appear in the payload exactly (`25` = `25.0`, but a
  bare `14` is not grounded by `14:05`, and no rounding). A figure that is a `stated` or
  `learned` weight must carry that label in its sentence. No LLM judge.
- **One retry, then the template.** A schema failure, an ungrounded number or a wrong
  label gets one retry with the errors listed (each request is capped at 20 s); after that, or without an
  `ANTHROPIC_API_KEY`, the guests read the deterministic template, which only prints
  figures from the payload. The proposal's provenance records which one wrote it
  (`prompt_versions["explain"]`: `explain-v1` or `template`).
- **English only for now.** The narrator writes English; with `lang` other than `en` (for
  example Spanish) the guests read the localized deterministic template, so the same
  grounding guarantees hold but the wording is fixed. The narrator payload names a guest
  who sits a ride out as `Guest N`, never by their free-text label ("tu mamá"); only the
  template, which stays in the process, uses the label.
- **Model:** `EXPLAIN_MODEL` (defaults to `ELICIT_MODEL`). Tests inject a fake
  narrator (`tests/explain_support.py`), so they never call Claude, even with a key in
  `.env`.

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
| `planner.*` (P0-27) | `build_plan`, `check_plan`, `replan`, `score_preferences`, `forecast_waits` |

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
violations name the rule and the stop (P0-27's diagnostics) though never the
requirement. That is the demo's single-machine boundary; serving it on a network
needs authentication first (with the API layer, P0-35a).

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

All four of the above run on every pull request via `.github/workflows/ci.yml` (P0-04).

## Implementation Status

| Component | Status | Details |
|---|---|---|
| **LangGraph State v2** | ✅ Done | Typed ParkMindState, state helpers, message reducer |
| **Preference Resolution** | ✅ Done | Weighted aggregation from GuestProfile (queue, walking, categories) |
| **Plan Synthesis** | ✅ Initial planning graph [P0-30] | ELICIT → CONFIRM → LOAD CONTEXT → RESOLVE GROUP → BUILD PLAN → CHECK → EXPLAIN → PROPOSE → APPROVAL. A candidate is proposed (and explained) only after it passes the ConstraintChecker; it is activated only when the human approval interrupt resumes with APPROVED. Runs on in-process use cases, no MCP server. APPROVE / EDIT / REJECT [P0-32]: an edit is re-planned, re-checked and proposed again. |
| **Explanations** | ✅ P0-31 | Claude explains the validated plan from a payload only (plan, diff, provenance, stated/learned preference weights); every numeral is checked against the payload, one retry, then the deterministic template. |
| **Event monitor** | ✅ P0-33 | Snapshot-to-event detector with per-reading freshness, `EventPolicy` (§25 table, confirmation gate, fatigue walking cap), cross-source dedup, supersede on significant events, `scripts/run_monitor.py`. Consumed by the replanning graph [P0-34]. |
| **MCP capability boundary** | ✅ P0-24..P0-27 | `parkmind-mcp`: 15 typed tools (`data.*`, `knowledge.*` with pgvector semantic search, `planner.*`); LOAD CONTEXT can read through it with in-process fallback. `planner.replan` takes the plan, its progress and the events inline (§31), so a client without a stored plan can replan. |
| **Demo app (API + UI)** | ✅ P0-35 / P0-36 | FastAPI layer over the graphs (`/api/*`, Swagger at `/docs`) and a React UI; bearer-token login, per-user session ownership, LLM usage caps, es/en conversation. Open: P0-32 edit path, #88 e2e bugs, guardrails, Langfuse. |
| **Weather Integration** | ✅ Done | OpenMeteo adapter (hourly forecast) |
| **Attractions Integration** | ✅ Done | ThemePark catalog adapter (rides, wait times) |
| **PostgreSQL Repos** | ✅ Done | Profiles, Plans, Session store (in-memory for accessibility) |
| **Initial Planning Graph** | ✅ Done | Superseded by the Plan Synthesis row above (P0-30). |
| **Constraint Checker** | ✅ Done | 11 rules, plus `check_replan` for a replanned day; PlanDiff generation |
| **Replanner** | ✅ P0-23 / P0-34 | Replanner (frozen completed stops, remaining walking budget, rest-first) and the replanning graph: events → policy → replan → check → diff → explain → propose → approval. |

## Working conventions

- **Branch per component, not per person** — `feat/planning-constraint-checker`,
  not `feat/collaborator_name-work`. Short-lived branches, PR to `main` within 2-4 days.
- **`main` is protected** — at least one review before merge.
- **Architecture decisions are frozen, not improvised.** If a PR needs to
  contradict something in the wiki's "Decisions Already Frozen" section,
  that's a team conversation first, a wiki changelog entry second, and code
  third — never the other way around.
