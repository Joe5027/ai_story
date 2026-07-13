# Done Definition

Last updated: 2026-07-13

## Purpose

Use this file to choose the strongest practical validation before closing AI Story work. Report what was validated, what was skipped, and why.

## Global Done Rules

- Do not claim a code, config, or workflow change is complete without a relevant check or an explicit validation limit.
- Prefer the smallest command that would catch the changed behavior.
- Keep generated media in `storage/` or configured external storage, not tracked source paths.
- Do not log or commit raw API keys, JWTs, OpenCode passwords, model-provider credentials, or generated private media paths.
- Do not claim that a paid-call path is safe unless tests prove all three fail-closed gates: explicit project cloud authorization, a currently effective versioned price, and an atomic project/global budget reservation. Every budget defaults to `0`.
- Do not treat subjective quality, queue delay, or a manually selected quality profile as a technical failure eligible for automatic paid fallback.
- If older docs and source disagree, validate against source and update `docs/project-map.md` or `docs/long-term-memory.md`.

## Backend Checks

Use from `backend/` unless noted.

- Import/config smoke: `uv run python manage.py check`
- Django smoke fallback: `python manage.py check`
- Django tests: `uv run python manage.py test`
- Targeted app test: `uv run python manage.py test apps.projects.tests.test_queue`
- SSE/ASGI/Celery contract tests: `uv run python manage.py test apps.projects.tests.test_celery_contract apps.projects.tests.test_sse_views apps.projects.tests.test_asgi_sse`
- Optional Redis Pub/Sub smoke from repo root: `node scripts/validate-streaming-local.cjs`
- Required Redis/ASGI/EventSource/Vue smoke from repo root: `node scripts/validate-streaming-local.cjs --require-redis`
- Real SiliconFlow video smoke from repo root: `SILICONFLOW_API_KEY=<redacted> uv run python scripts/smoke-siliconflow-video.py`
- Pytest route when dependencies are available: `pytest --cov apps --cov core`
- Migration check before model changes: `uv run python manage.py makemigrations --check --dry-run`
- Hybrid inference unit/API/scheduler route: `uv run python manage.py test apps.inference.tests apps.models.tests apps.ai_proxy.tests`
- Project hybrid endpoint route: `uv run python manage.py test apps.inference.tests.test_api`
- Disposable SQLite migration: set `SQLITE_DB_PATH` to a temporary file, then run `uv run python manage.py migrate --noinput` and targeted tests.
- Disposable PostgreSQL migration: set a non-production `DATABASE_URL=postgresql://...`, run `uv run python manage.py migrate --noinput`, then verify table counts/constraints and run concurrency tests. SQLite-only evidence is insufficient for `select_for_update` claims.
- Celery worker route: `uv run celery -A config worker -l info`
- ASGI/SSE route: `./run_asgi.sh` or `daphne -b 0.0.0.0 -p 8000 config.asgi:application`

Known current validation notes:

- `uv run python manage.py check` passed after `backend/config/urls.py` was changed to mount optional `apps.mcp.urls` and `apps.agent.urls` only when those modules are present.
- Page-assistant and MCP API behavior is not proven unless source or compiled outputs exist under `backend/apps/agent/` and `backend/apps/mcp/`.
- `npm ci`, `npm run lint`, and `npm run build` passed on 2026-07-01.
- Frontend lint currently passes with no ESLint warnings after applying autofixes and moving `beforeDestroy()` before `methods` in `ScreenplayDetail.vue`.
- Frontend production build currently passes with no webpack warnings after removing the duplicate generated Tailwind import, extracting production CSS, removing the `process.env.NODE_ENV` DefinePlugin override, and setting an explicit production performance budget.
- Frontend is now on Vue 3 / Vue Router 4 / Vuex 4 / `vue-loader` 17 / `@vue/compiler-sfc`; `npm audit --json` currently reports 0 vulnerabilities.
- Browser smoke has verified unauthenticated redirect/login shell behavior after the Vue 3 migration.
- `npm run smoke:auth` now performs a disposable authenticated browser smoke with a temporary SQLite DB and seeded API data. It verifies login, `/series`, `/projects/:id`, the project canvas asset drawer, `/prompts`, and `/models`, then cleans up local processes and temp data.
- `.github/workflows/validation.yml` now runs the pre-PR validation gate on pull requests, pushes to `main`/`release`, and manual dispatch.
- `node scripts/validate-local.cjs` mirrors the validation workflow locally and includes Redis-free SSE/Celery contract tests.
- `node scripts/validate-streaming-local.cjs` runs SSE/ASGI/Celery contracts, Redis Pub/Sub, and the authenticated Daphne/EventSource/Vue smoke. Without `--require-redis`, Redis-dependent checks skip when Redis is unavailable. `.github/workflows/streaming-redis-validation.yml` runs with a Redis service and `REQUIRE_REDIS=1` on manual dispatch and streaming-critical pull requests.
- Local required Redis smoke currently needs either a running Redis service or Docker Desktop Linux daemon; a Docker-based attempt on 2026-07-03 was blocked because the daemon was not available.
- Required Redis smoke passed on 2026-07-03 using a user-supplied external Redis endpoint through transient environment variables. Do not commit Redis credentials or full connection URLs.
- Real SiliconFlow video smoke passed on 2026-07-03 using a user-supplied API key through transient environment variables. The generated MP4 was downloaded under `storage/video`; do not commit API keys or temporary signed result URLs.
- The hybrid implementation has a Mock-safe seed migration: two local resource nodes, profiles/routes for the five supported capabilities, inactive local providers, and zero budgets. Applying the migration is not proof that a real model is installed or usable.

## Runtime Agent And Windows Runtime Checks

Use from `runtime_agent/` unless noted.

- Locked install: `uv sync --frozen --extra test`
- Full Agent contract: `uv run --extra test pytest`
- Dependency-light real-adapter configuration tests: `python -m pytest -q offline_tests`
- Core journal/scheduler smoke: `python scripts/core_smoke.py`
- Runtime process: `uv run python -m runtime_agent`
- Windows script safety from repo root: `pwsh -NoProfile -File scripts/local-ai/Test-LocalAIScripts.ps1`
- Read-only machine preflight: `pwsh -NoProfile -File scripts/local-ai/Test-Prerequisites.ps1`
- Side-effect preview: run every mutating local-AI script with `-WhatIf` or `-DryRun` before applying it.

The Runtime Agent contract is done only when auth, idempotency conflict, job create/query/cancel, restart recovery, resource mutual exclusion, artifact size/SHA-256 validation, and stable error envelopes pass. This proves the Agent control plane. It does not prove Ollama/ComfyUI/LightX2V model quality, license suitability, GPU memory safety, or overnight throughput.

Model/package installation is never an automated test prerequisite. Each package needs an approved license/revision/size/SHA-256 manifest and an explicit per-package confirmation; model and workflow upgrades must retain rollback material.

## Local AI Privacy, Cost, And Recovery Gates

Before enabling `AI_ROUTER_V2_ENABLED` for a canary project, prove all of the following with request spies and persisted ledger state:

- local success produces no external Provider request;
- local technical failure plus no cloud authorization produces zero external POSTs;
- cloud authorization plus project/global budget `0` produces zero external POSTs;
- authorization and budget plus no effective price produces zero external POSTs;
- all three gates passing invokes at most the configured paid fallback count and records price version, reservation, settlement, fallback source/reason, and idempotency key;
- ambiguous post-submit crashes keep the reservation for manual review and do not submit again automatically;
- worker/Agent/Redis restart recovers work items without duplicate media or duplicate billing;
- API key serializers never return full secrets, and logs/CSV exports contain neither keys nor full sensitive prompts/media.

Use PostgreSQL for concurrency evidence and a real Redis instance for lease/recovery evidence. Mock/SQLite tests remain valuable but cannot replace those integration gates.

## Local AI Benchmark Gate

- Canonical secret-free inputs: `benchmarks/local-ai/manifest.json` and its three referenced case files.
- Run outputs: `storage/benchmark-runs/<timestamp>/` (not tracked).
- Report target: `docs/local-ai/BENCHMARK_REPORT.md`.

Every run must record Git commit, OS/GPU/RAM, model digest, workflow version/hash, seed, input-case revision, timing, peak VRAM/RAM, terminal status, and artifact hashes. Never fill missing measurements with estimates. Until the current Windows machine passes the relevant cases, real local profiles stay unavailable; in particular, `final` video must not be shown as locally available based only on Mock tests or model vendor claims.

## AI Operating-Surface Checks

- Fast contract check from repo root: `node scripts/validate-ai-harness.cjs`
- Run it after changing `AGENTS.md`, `.codex/skills/`, project-map/knowledge/memory/automation docs, workflow stage membership, optional agent/MCP route wiring, or validation scripts.
- The check is dependency-free and fails on missing canonical surfaces, cross-layer stage membership drift, legacy Celery truth, unguarded optional routes, incomplete handoff sections, write-capable default automation wording, or missing local/CI integration.
- Keep the global workspace audit as a complementary structural check; it does not replace repository-specific source-to-doc contracts.
- The 2026-07-13 baseline passed 20 contract checks and repaired a missing `ASSET_EXTRACTION` member in `frontend/src/utils/constants.js`.
- The Redis-backed streaming gate includes `test_asgi_sse`, a real Redis Pub/Sub round-trip, and authenticated Playwright success/error flows through Daphne and Vue. A non-terminal `done` must keep an all-stage connection alive; only `pipeline_done` or `pipeline_error` may clear it.
- Local 2026-07-13 evidence: the required Redis streaming gate passed both terminal branches in 58.8 seconds, and the full non-Redis pre-PR gate passed in 82 seconds after `test_asgi_sse` was added.
- Remote 2026-07-13 evidence: pull request #1 run number 3 passed both `Validation` and `Streaming Redis Validation` on GitHub's Ubuntu 24.04 runner.

## Frontend Checks

Use from `frontend/`.

- Install dependencies: `npm install`
- Lint: `npm run lint`
- Fix formatting/lint issues when appropriate: `npm run lint:fix`
- Production build: `npm run build`
- Dev server: `npm run dev`
- Vue compatibility inventory: `npm run audit:vue2`
- Authenticated browser smoke: `npm run smoke:auth`
- Full local validation gate from repo root: `node scripts/validate-local.cjs`

For `/local-ai/*` changes, lint/build are the minimum. The stronger browser route must cover runtime-node health, route/price editing, zero-default budgets, cloud authorization confirmation, estimates, work-item retry/cancel, paid-regeneration confirmation, ledger filtering/CSV redaction, and API keys never being echoed.

Known current validation risk:

- `webpack-dev-server` is now 5.2.5, `devServer.proxy` uses the v5 array schema, and the frontend Node engine is now `>=18.12.0`.
- A `sockjs -> uuid` npm override removes the dev-server `uuid` audit chain; verify with `npm ls webpack-dev-server sockjs uuid`.
- Authenticated browser flows have a repeatable local validation path through `npm run smoke:auth`. Expected optional `apps.agent` route 404s are filtered because those routes are inactive in this checkout; other browser console or network failures still fail the smoke.

## Change-Type Matrix

| Change type | Minimum check | Stronger check |
| --- | --- | --- |
| Rules/skills/project docs | `node scripts/validate-ai-harness.cjs` | Harness check plus workspace audit and handoff validation |
| Backend model/migration | `uv run python manage.py makemigrations --check --dry-run` | Targeted Django tests plus migration apply on disposable DB |
| Hybrid route/privacy/price/budget | `apps.inference.tests` plus request-spy denial tests | PostgreSQL concurrent reservations and one controlled paid fallback |
| Work item/scheduler | Work-item state/lease/idempotency tests | PostgreSQL + Redis worker/Agent restart and no-duplicate billing/media drill |
| Runtime Agent | `uv run --extra test pytest` | Pinned real adapters plus restart/cancel/artifact checks on target hardware |
| Windows runtime scripts | `Test-LocalAIScripts.ps1` | Fresh Windows dry-run, confirmed install, backup/log collection, and rollback rehearsal |
| Local model/workflow | Secret-free case contract review | Target-machine `benchmarks/local-ai/` run with model/workflow hashes and resource capture |
| Backend API/ViewSet | Targeted `manage.py test` for affected app | API smoke with authenticated request |
| Celery stage/task | Targeted task or queue tests plus `test_celery_contract` | Worker + Redis local run |
| SSE or Redis Pub/Sub | `test_sse_views`, `test_asgi_sse`, and optional `validate-streaming-local.cjs` | `validate-streaming-local.cjs --require-redis` with real Redis, Daphne, EventSource, and Vue UI |
| AI client/executor | Unit test with mock provider | Provider sandbox call with redacted logs |
| Prompt/template logic | Prompt tests or serializer tests | UI debug workbench smoke |
| Vue route/component | `npm run lint` | `npm run build` plus browser screenshot/manual flow |
| Local AI control panel | `npm run lint` and `npm run build` | Authenticated browser coverage for nodes/routes/budget/privacy/work items/ledger |
| UI visual change | Existing visual baseline in `PromptList.vue` reviewed | Screenshot on desktop and mobile |
| Docs/control surface | `node scripts/validate-ai-harness.cjs` | Workspace audit script plus handoff validation when memory changes |
| Automation prompt | Read-only dry review of prompt contract | Trial run that produces only `Facts`, `Checks Run / Checks Missing`, `Risk`, `Next Highest-Value Action` |
| Pre-PR validation gate | `node scripts/validate-local.cjs` | GitHub Actions `Validation` workflow on PR |

## Completion Report Shape

For non-trivial work, close with:

- Change made
- Checks run
- Checks missing or blocked
- Risk
- Next highest-value action

For environment or harness work, include:

- Agents used
- Surfaces added or updated
- Validation
- Residual risk

## Current First Fix Gate

Before doing page-assistant or MCP feature validation, decide how `apps.agent` and `apps.mcp` should exist in this workspace:

1. restore their intended source files,
2. restore compiled `.so` or `.pyd` outputs,
3. or explicitly accept that those optional routes are inactive in this checkout.

Do not treat `manage.py check` passing as proof that those optional APIs work.
