# AI Story Project Map

Last updated: 2026-07-13

## Purpose

This is the fast navigation map for AI-assisted work in this repository. Use it before broad code search. It records current source truth, known documentation drift, and the strongest validation entry points.

## Product Shape

AI Story is a Django + Vue application for converting story input into video assets.

Core generation flow:

`rewrite -> asset_extraction -> storyboard -> image_generation | multi_grid_image -> image_edit -> camera_movement -> video_generation`

The legacy public README describes a five-stage flow. Current source adds `asset_extraction`, `multi_grid_image`, and `image_edit`. Use `PROJECT_STAGE_TYPES`, `ProjectStage.STAGE_TYPES`, prompt choices, and frontend constants as stage-membership truth; use `get_project_stage_order()` in `backend/apps/projects/utils.py` as execution-order truth.

## Top-Level Layout

- `backend/`: Django 3.2 backend, DRF APIs, Celery tasks, Redis Pub/Sub, AI client abstractions.
- `frontend/`: Vue 3 SPA with Vue Router 4, Vuex 4 modules, API clients, canvas/project/prompt/model views.
- `docs/`: repo-local design docs and agent operating surfaces.
- `backend/docs/`: deeper workflow notes, especially Jianying and multi-grid image planning.
- `docker/`, `docker-compose.yml`, `docker-compose-deploy.yml`: local and deployment container paths.
- `storage/`: generated media target, intentionally outside Git.
- `.github/workflows/docker-image.yml`: Docker image build and push workflow for the `release` branch.
- `.github/workflows/validation.yml`: pre-PR validation workflow for backend import/config health, SSE/Celery contracts, and frontend audit/lint/build/authenticated smoke.
- `.github/workflows/streaming-redis-validation.yml`: Redis-backed Daphne/Playwright validation, available manually and automatically on PRs that touch streaming-critical paths.
- `scripts/validate-ai-harness.cjs`: dependency-free contract check for required AI operating surfaces, stage membership, Celery/optional-route truth, handoff sections, automation guardrails, and local/CI validation wiring.
- `scripts/validate-local.cjs`: local pre-PR validation aggregate mirroring the validation workflow.
- `scripts/validate-streaming-local.cjs`: local SSE/Celery/Redis/ASGI/browser validation; Redis-dependent checks skip unless Redis is available or `--require-redis` is passed.
- `scripts/smoke-siliconflow-video.py`: optional real SiliconFlow video smoke; requires `SILICONFLOW_API_KEY` in the process environment and writes generated videos under `storage/video`.

## Backend Map

- `backend/config/settings/`: layered settings. `base.py` defines installed apps, REST framework defaults, Redis DB split, Celery, CORS, page-agent env vars, and JWT.
- `backend/config/urls.py`: root URL includes `projects`, `prompts`, `models`, `content`, `users`, `ai_proxy`, `scripts`, and `mock_api`. Optional closed-source/generated `apps.mcp.urls` and `apps.agent.urls` are mounted only when their modules are present.
- `backend/config/celery_app.py`: actual Celery app. Some older docs mention `config/celery.py`; that file is absent.
- `backend/config/asgi.py`: wraps Django with `ProjectSSEASGIApplication` for non-blocking project SSE under Daphne.
- `backend/apps/projects/asgi_sse.py`: async ASGI adapter for the two project SSE paths; it offloads the synchronous Redis subscriber without blocking the ASGI event loop.
- `backend/apps/projects/`: series, projects, workflow stages, queueing, stage execution endpoints, SSE views, Celery task dispatch.
- `backend/apps/prompts/`: prompt sets, prompt templates, global variables/assets, prompt debugging and artifacts.
- `backend/apps/models/`: AI provider records, vendor discovery/batch creation, provider connection tests, usage logs, OpenCode config helpers.
- `backend/apps/content/`: generated content models, storage image/video APIs, stage processors.
- `backend/apps/scripts/`: screenplays and screenplay episodes.
- `backend/apps/users/`: login/register/logout/profile/password APIs.
- `backend/apps/ai_proxy/`: OpenAI-compatible chat/images/videos proxy endpoints and file upload.
- `backend/apps/mock_api/`: mock LLM/image/video endpoints for local testing.
- `backend/core/ai_client/`: AI client interfaces and concrete executors.
- `backend/core/ai_client/siliconflow_video_client.py`: SiliconFlow `/v1/video/submit` + `/v1/video/status` video task client.
- `backend/core/pipeline/`: generic pipeline abstractions; current project execution primarily uses `apps.projects.tasks`.
- `backend/core/redis/`: Redis Pub/Sub publisher/subscriber and SSE integration notes.
- `backend/core/services/`: cross-cutting services such as Jianying draft generation.

## Frontend Map

- `frontend/src/router/index.js`: route source truth. Root redirects to `/series`; protected areas are `series`, `projects`, `screenplays`, `prompts`, `assets`, and `models`.
- `frontend/src/store/index.js`: Vuex module registry. Current modules live under `frontend/src/store/modules/`.
- `frontend/src/api/`: typed-by-convention API clients for projects, prompts, models, content, screenplays, auth, agent.
- `frontend/src/services/apiClient.js`: Axios base client and auth refresh path.
- `frontend/src/services/sseService.js`: EventSource wrapper for project-stage and project-wide SSE.
- `frontend/src/services/pageAgent/`: page assistant context, action registry, and agent service helpers.
- `frontend/src/views/projects/`: series/project list, detail, create, edit pages.
- `frontend/src/components/canvas/`: workflow canvas nodes for storyboard, image, image edit, multi-grid, camera, video, and chat drawer.
- `frontend/src/views/prompts/PromptList.vue`: visual baseline for project management and canvas-adjacent UI, per `AGENTS.md`.
- `frontend/scripts/vue2-compat-audit.cjs`: repeatable Vue 2 migration inventory; run with `npm run audit:vue2`.
- `frontend/scripts/authenticated-smoke.cjs`: self-contained authenticated browser smoke; run with `npm run smoke:auth` from `frontend/`.
- `frontend/scripts/streaming-authenticated-smoke.cjs`: temporary-DB authenticated Redis → Daphne → EventSource → Vue smoke; run with `npm run smoke:streaming` when Redis is available.

## Runtime And Integration Notes

- Backend default development DB is SQLite at `backend/data/ai_story.db` unless `SQLITE_DB_PATH` overrides it.
- `npm run smoke:auth` creates a temporary SQLite database, runs migrations, seeds a smoke user/series/project/prompt set, starts backend and frontend on free local ports, verifies authenticated pages with Playwright, and cleans up afterward.
- `frontend/config/webpack.dev.js` reads `BACKEND_PORT` or `BACKEND_TARGET` for dev-server proxy routing; default remains `http://127.0.0.1:8010`.
- `node scripts/validate-ai-harness.cjs` is the fast, dependency-free gate for repo operating-surface drift and cross-layer stage membership.
- `node scripts/validate-local.cjs` runs the local pre-PR gate: the AI workspace contract, `uv run python manage.py check`, SSE/ASGI/Celery contract tests, `npm audit --audit-level=low`, `npm run lint`, `npm run audit:vue2`, `npm run build`, and `npm run smoke:auth`.
- `node scripts/validate-streaming-local.cjs --require-redis` proves Redis Pub/Sub plus success/error messages through Daphne, EventSource, the frontend SSE client, and project-detail UI.
- Redis DB split in `settings/base.py`: Celery broker `/0`, Celery result `/1`, Pub/Sub `/2`, Django cache `/4`.
- ASGI is preferred for real SSE behavior. Django 3.2 synchronously iterates `StreamingHttpResponse` under ASGI, so project SSE paths must stay routed through `ProjectSSEASGIApplication`; plain Django ASGI handling is not a valid streaming fallback.
- Celery tasks are defined in `backend/apps/projects/tasks.py` and import `config.celery_app.app`.
- Page assistant/OpenCode settings live in `settings/base.py` and `docs/agent_api.md`.

## Known Drift And Risk

- `backend/apps/agent/` and `backend/apps/mcp/` are intentionally ignored except `.gitkeep` and compiled extension outputs. Their URL modules are optional in `backend/config/urls.py`; if compiled modules are restored, verify the routes mount.
- Historical `backend/config/celery.py` and `ProjectDetailNew.vue` references were corrected in the primary docs touched during the 2026-07-01 enhancement pass. Continue checking older docs before copying paths.
- Some existing docs describe feature state from an earlier phase. Prefer current source and tests over status checklists in older docs.
- Vue 3 is the active frontend runtime. Use `docs/vue3-migration-plan.md` and `npm run audit:vue2` before changing Vue, router, store, loader, compiler packages, or considering a Pinia rewrite.

## High-Signal Search Seeds

- Stage order and enabled templates: `backend/apps/projects/utils.py`
- Stage execution endpoint: `backend/apps/projects/views.py` around `execute_stage`
- Full pipeline task: `backend/apps/projects/tasks.py` around `run_full_pipeline_task`
- SSE backend: `backend/apps/projects/sse_views.py`, `backend/core/redis/`
- SSE frontend: `frontend/src/services/sseService.js`
- Prompt variables/assets: `backend/apps/prompts/models.py`, `frontend/src/api/prompts.js`
- Model provider tests: `backend/apps/models/tests/`
- Project queue tests: `backend/apps/projects/tests/test_queue.py`
- Streaming tests: `backend/apps/projects/tests/test_sse_views.py`, `backend/apps/projects/tests/test_celery_contract.py`, `backend/apps/projects/tests/test_redis_pubsub.py`

## First Validation Handle

Before claiming page-assistant or MCP API health, confirm whether compiled/source modules exist under `backend/apps/agent/` and `backend/apps/mcp/`. Before claiming frontend lint health, ensure `frontend/node_modules` exists via `npm install`.
