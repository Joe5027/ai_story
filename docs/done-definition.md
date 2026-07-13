# Done Definition

Last updated: 2026-07-13

## Purpose

Use this file to choose the strongest practical validation before closing AI Story work. Report what was validated, what was skipped, and why.

## Global Done Rules

- Do not claim a code, config, or workflow change is complete without a relevant check or an explicit validation limit.
- Prefer the smallest command that would catch the changed behavior.
- Keep generated media in `storage/` or configured external storage, not tracked source paths.
- Do not log or commit raw API keys, JWTs, OpenCode passwords, model-provider credentials, or generated private media paths.
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

Known current validation risk:

- `webpack-dev-server` is now 5.2.5, `devServer.proxy` uses the v5 array schema, and the frontend Node engine is now `>=18.12.0`.
- A `sockjs -> uuid` npm override removes the dev-server `uuid` audit chain; verify with `npm ls webpack-dev-server sockjs uuid`.
- Authenticated browser flows have a repeatable local validation path through `npm run smoke:auth`. Expected optional `apps.agent` route 404s are filtered because those routes are inactive in this checkout; other browser console or network failures still fail the smoke.

## Change-Type Matrix

| Change type | Minimum check | Stronger check |
| --- | --- | --- |
| Rules/skills/project docs | `node scripts/validate-ai-harness.cjs` | Harness check plus workspace audit and handoff validation |
| Backend model/migration | `uv run python manage.py makemigrations --check --dry-run` | Targeted Django tests plus migration apply on disposable DB |
| Backend API/ViewSet | Targeted `manage.py test` for affected app | API smoke with authenticated request |
| Celery stage/task | Targeted task or queue tests plus `test_celery_contract` | Worker + Redis local run |
| SSE or Redis Pub/Sub | `test_sse_views`, `test_asgi_sse`, and optional `validate-streaming-local.cjs` | `validate-streaming-local.cjs --require-redis` with real Redis, Daphne, EventSource, and Vue UI |
| AI client/executor | Unit test with mock provider | Provider sandbox call with redacted logs |
| Prompt/template logic | Prompt tests or serializer tests | UI debug workbench smoke |
| Vue route/component | `npm run lint` | `npm run build` plus browser screenshot/manual flow |
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
