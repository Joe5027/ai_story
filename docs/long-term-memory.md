# Long-Term Memory

## Facts

- Workspace: `D:\Code\AI\worksp\project\ai_story`.
- Audit date: 2026-07-13.
- Global audit reported no high-signal drift.
- Workspace audit reported missing required surfaces: `docs/project-map.md`, `docs/done-definition.md`, and `.codex/skills/`.
- Current backend stack is Django 3.2, DRF, Celery, Redis, SQLite/PostgreSQL, and AI provider abstractions.
- Current frontend stack is Vue 3.5, Vue Router 4, Vuex 4, webpack, Tailwind CSS, and daisyUI.
- Current workflow stages include `rewrite`, `asset_extraction`, `storyboard`, `image_generation`, `multi_grid_image`, `camera_movement`, `video_generation`, and `image_edit`.
- `backend/config/urls.py` conditionally mounts `apps.mcp.urls` and `apps.agent.urls`; those URL files are absent in the current tree, so the optional routes are inactive.
- `frontend/node_modules` was absent during the initial audit; later `npm ci` installed dependencies successfully without changing tracked files.
- `uv run python manage.py check` timed out during the initial audit run, then passed after the backend environment was warmed by `uv`.
- Frontend dependency and Vue migration cleanup reduced full `npm audit` exposure from 38 vulnerabilities to 0.
- Frontend production build size dropped after removing the duplicate generated Tailwind import and enabling production CSS extraction; the entrypoint is now under the explicit 512 KiB production performance budget.
- `webpack-dev-server` is now 5.2.5; frontend `engines.node` is now `>=18.12.0`.
- `npm run audit:vue2` scans the Vue compatibility surface; current Vue 3 baseline has 0 Vue global API, 0 Vue 2 lifecycle, 0 `$set/$delete`, 70 Vuex store/helper inventory items, and 0 Vue 2 template API matches.
- `npm run smoke:auth` is the repeatable authenticated browser smoke. It creates a temporary SQLite DB, runs migrations, seeds a smoke user/series/project/prompt set, starts backend/frontend on local ports, verifies authenticated pages with Playwright, and cleans up temp data and processes.
- `node scripts/validate-local.cjs` is the local pre-PR validation aggregate. It runs backend Django check, SSE/Celery contract tests, frontend npm audit, lint, Vue 2 compatibility inventory, production build, and authenticated browser smoke.
- `node scripts/validate-streaming-local.cjs` validates SSE/Celery contracts plus a Redis Pub/Sub smoke that skips when Redis is unavailable. Use `--require-redis` when Redis must be present.
- `uv run python scripts/smoke-siliconflow-video.py` is the optional real SiliconFlow video smoke. It requires `SILICONFLOW_API_KEY` in the process environment and downloads successful result URLs into `storage/video`.
- `.github/workflows/validation.yml` mirrors the local gate for pull requests, pushes to `main`/`release`, and manual dispatch.
- `.github/workflows/streaming-redis-validation.yml` is a Redis-backed streaming validation with `REQUIRE_REDIS=1`; it supports manual dispatch and path-scoped pull-request runs for streaming-critical changes.
- `backend/apps/projects/asgi_sse.py` is the non-blocking ASGI adapter for project SSE. Django 3.2.15 synchronously iterates `StreamingHttpResponse` inside `ASGIHandler.send_response()`, which otherwise blocks Daphne from flushing EventSource chunks while Redis polling is active.
- `frontend/scripts/streaming-authenticated-smoke.cjs` verifies authenticated success and error streams through real Redis, Daphne, EventSource, the frontend SSE client, and `ProjectDetail.vue` UI.
- `scripts/validate-ai-harness.cjs` is the dependency-free AI operating-surface gate. It validates required rules/skills/docs, cross-layer stage membership, execution-order coverage, Celery and optional-route truth, handoff/automation contracts, and local/CI wiring.
- The 2026-07-13 workspace audit reported no high-signal structural drift, but the new source-aware harness check found `asset_extraction` missing from `frontend/src/utils/constants.js`; the constant was repaired.

## Decisions

- Keep repo-specific AI operating guidance in project-local docs and `.codex/skills`, with `AGENTS.md` as the thin entrypoint.
- Treat source code and the new project map as preferred truth when older docs disagree.
- Do not create runtime stubs for `apps.agent` or `apps.mcp`; their directories are ignored except `.gitkeep` and compiled extension outputs, so root URLConf now degrades safely when modules are absent.
- Keep automation guidance read-only by default.
- Prefer targeted validation by change type instead of broad full-suite claims when dependencies or route gaps block execution.
- Keep Vuex 4 as the current low-risk state layer unless the user explicitly chooses a larger Pinia rewrite.
- Treat optional `/api/v1/agent/*` 404s as expected only when `apps.agent.urls` is absent; the authenticated smoke filters those expected route misses but still fails on other browser console or network errors.
- Keep the local validation script and GitHub Actions workflow aligned; update both when the done definition changes.
- Treat `PROJECT_STAGE_TYPES`, Django model/prompt choices, and frontend constants as stage-membership truth. Treat `get_project_stage_order()` as execution-order truth; do not force presentation lists into execution order.
- Run the AI harness contract first in local and CI validation so control-surface drift fails before dependency installation and expensive application checks.
- Keep the synchronous Django SSE views for WSGI behavior and focused unit tests, but keep the two ASGI SSE paths routed through `ProjectSSEASGIApplication` until Django is upgraded and native async streaming is revalidated.
- Treat `VUE_APP_API_BASE_URL` as the complete API prefix (`/api/v1` by default); SSE URLs append `/projects/...` and must not add a second `/api/v1`.

## Assumptions

- The user wants durable workspace capability improvements over one-off explanation.
- Page-assistant and MCP API behavior remains unproven unless external/generated source or compiled modules are restored.
- The local package environment now supports backend `uv run` checks and frontend lint/build/dev-server/browser smoke after Vue 3 migration.
- Existing generated images and media at repository root are historical artifacts; new generated media should remain under `storage/` or configured media roots.
- The harness parser assumes canonical stage declarations remain literal Python/JavaScript lists or objects. If those declarations become generated dynamically, update the parser in the same change.

## Validation

- Ran `runtime_preflight.py --format text`; shell, PowerShell, bundled plugins, and exposed session tools were reported, with a stale selected remote-host warning.
- Ran `audit_environment.py --mode workspace --workspace D:\Code\AI\worksp\project\ai_story --format text`; it identified the missing workspace contract surfaces.
- Ran `audit_environment.py --mode global --format text`; it reported no high-signal global drift.
- Confirmed `backend/apps/mcp/urls.py`, `backend/apps/agent/urls.py`, and `backend/config/celery.py` are absent; confirmed `backend/config/celery_app.py`, frontend store modules, and `ProjectDetail.vue` are present.
- `npm run lint -- --quiet` was attempted and failed because `eslint` was unavailable.
- `uv run python manage.py check` was attempted and timed out.
- After adding the workspace contract surfaces, reran `audit_environment.py --mode workspace --workspace D:\Code\AI\worksp\project\ai_story --format text`; it reported no missing checks and no high-signal drift.
- Validated this file with `validate_handoff_contract.py`; result was valid.
- Confirmed `apps.agent` and `apps.mcp` namespace packages import, while `apps.agent.urls` and `apps.mcp.urls` are absent.
- Updated `backend/config/urls.py` to mount optional closed-source/generated route modules only when `find_spec()` can locate them.
- Reran `uv run python manage.py check`; result: `System check identified no issues`.
- Added `backend/config/tests/test_urls.py` to cover `optional_include()` for missing and existing modules.
- Ran `uv run python manage.py test config.tests.test_urls`; result: 2 tests passed.
- Corrected primary doc references from `config/celery.py` to `config/celery_app.py` and from `ProjectDetailNew.vue` to `ProjectDetail.vue`.
- Ran `npm ci`; it installed 680 packages and reported 36 vulnerabilities plus Vue 2 / ESLint 8 EOL warnings.
- Ran `npm run lint`; it passed with 0 errors and 77 warnings.
- Ran `npm run build`; it passed with webpack warnings for bundle size, `process.env.NODE_ENV` DefinePlugin conflict, outdated Browserslist data, and baseline-browser-mapping age.
- Ran `npm audit fix` without `--force`; it updated patch/minor dependency versions inside allowed ranges and reduced full audit exposure to 9 vulnerabilities.
- Updated webpack config to stop defining `process.env.NODE_ENV` manually, use `MiniCssExtractPlugin.loader` for production CSS, set `NODE_ENV` before loading common webpack config, and set an explicit production performance budget.
- Removed `import './output.css'` from `frontend/src/main.js`; Tailwind is already pulled through `frontend/src/assets/css/main.css`.
- Ran `npm run lint:fix` and manually fixed the remaining Vue lifecycle order warning; `npm run lint` now passes with no warnings.
- Ran `npm run build`; it now passes with no webpack warnings.
- Ran `npm audit --omit=dev --json`; it still exits non-zero because Vue 2 / Vuex 3 leave 2 low production vulnerabilities.
- Ran `npm audit --json`; it still exits non-zero with 9 total vulnerabilities, all requiring breaking migration decisions.
- Upgraded `webpack-dev-server` to 5.2.5, changed `devServer.proxy` to the v5 array schema, raised frontend Node engine to `>=18.12.0`, and added a `sockjs -> uuid@^11.1.1` override.
- Verified `npm ls webpack-dev-server sockjs uuid`; result: `webpack-dev-server@5.2.5 -> sockjs@0.3.24 -> uuid@11.1.1`.
- Started webpack-dev-server 5 with `--no-open --host 127.0.0.1 --port 13002`; logs showed proxy creation, loopback URL, history fallback, and successful webpack compile. The temporary dev-server processes were stopped afterward.
- Added `frontend/scripts/vue2-compat-audit.cjs` and `npm run audit:vue2`; current output scanned 80 files and found 126 Vue 2 migration markers.
- Added `docs/vue3-migration-plan.md` as the durable migration plan and validation gate.
- Ran `npm audit --json`; it now reports 6 total vulnerabilities: 2 low and 4 moderate, all tied to Vue 2 / Vuex 3 / Vue loader compiler migration.
- Upgraded frontend runtime to Vue 3.5.39, Vue Router 4.6.4, Vuex 4.1.0, `vue-loader` 17.4.2, and `@vue/compiler-sfc` 3.5.39; removed `vue-template-compiler`.
- Migrated `src/main.js` to `createApp`, `app.use(router)`, `app.use(store)`, and `app.config.globalProperties`.
- Migrated `src/router/index.js` to `createRouter(createWebHistory(...))`, Vue Router 4 catch-all route syntax, and `{ left, top }` scroll behavior.
- Migrated `src/store/index.js` to `createStore`.
- Updated webpack Vue alias to `vue/dist/vue.runtime.esm-bundler.js`, added Vue feature flags, and switched dev CSS injection from removed `vue-style-loader` to existing `style-loader`.
- Migrated `confirm.js` from `Vue.observable` / `Vue.nextTick` to `reactive` / `nextTick`.
- Replaced remaining `beforeDestroy` with `beforeUnmount`; replaced remaining `this.$set` / `this.$delete` calls with direct assignment/deletion.
- Ran browser smoke against `http://127.0.0.1:13011/`; root redirected to `/login?redirect=/series`, `#app` mounted with login text, and captured browser error logs were empty.
- Ran `npm audit --json`; result: 0 vulnerabilities.
- Added `frontend/scripts/authenticated-smoke.cjs`, `npm run smoke:auth`, Playwright devDependency, and `BACKEND_PORT` / `BACKEND_TARGET` support in `frontend/config/webpack.dev.js`.
- Ran `npm run smoke:auth`; result: passed. It verified login, `/series`, `/projects/:id`, project canvas asset drawer, `/prompts`, and `/models` against disposable seeded data.
- Added `scripts/validate-local.cjs` and `.github/workflows/validation.yml`.
- Added `backend/apps/projects/tests/test_sse_views.py`, `backend/apps/projects/tests/test_celery_contract.py`, and `backend/apps/projects/tests/test_redis_pubsub.py`.
- Added `scripts/validate-streaming-local.cjs` and `.github/workflows/streaming-redis-validation.yml`.
- Updated `scripts/validate-local.cjs` and `.github/workflows/validation.yml` to include Redis-free SSE/Celery contract tests.
- Ran `uv run python manage.py test apps.projects.tests.test_celery_contract apps.projects.tests.test_sse_views`; result: 5 tests passed.
- Ran `node scripts/validate-streaming-local.cjs`; result: passed, with Redis Pub/Sub smoke skipped because no local Redis was available.
- Ran `node scripts/validate-local.cjs`; result: passed. It covered backend check, SSE/Celery contract tests, frontend audit, lint, Vue 2 inventory, production build, and authenticated browser smoke.
- Attempted `node scripts/validate-streaming-local.cjs --require-redis` through a temporary Docker Redis container on host port 16379, but Docker Desktop Linux daemon was unavailable; required Redis-backed smoke remains unvalidated locally.
- Reran `node scripts/validate-streaming-local.cjs --require-redis` using a user-supplied external Redis endpoint via transient environment variables; result: passed. No Redis credential was written to repository files.
- Added `backend/core/ai_client/siliconflow_video_client.py`, registered it in model provider choices and the SiliconFlow vendor catalog, and wired it into provider connection testing.
- Added `scripts/smoke-siliconflow-video.py` for transient-key real video smoke runs.
- Ran `uv run python manage.py test apps.models.tests.test_image2video_client`; result: 8 tests passed.
- Ran targeted SiliconFlow vendor tests in `apps.models.tests.test_vendor_batch`; result: 2 tests passed.
- Ran a real SiliconFlow T2V smoke with a user-supplied API key passed through transient environment variables; result: passed. The generated MP4 was downloaded under `storage/video/2026-07-03/`, with no API key written to repository files.
- Verified the generated MP4 with `ffprobe`: H.264, 1280x720, approximately 5.06 seconds, approximately 1.79 MB.
- Ran `node scripts/validate-ai-harness.cjs`; the first run failed on the missing frontend `asset_extraction` constant, proving the gate detects real drift.
- Added `ASSET_EXTRACTION: 'asset_extraction'` to `frontend/src/utils/constants.js`, reran the harness, and passed all 20 checks.
- Ran `node --check scripts/validate-ai-harness.cjs` and `node --check scripts/validate-local.cjs`; both scripts passed syntax validation.
- Ran `node scripts/validate-local.cjs` after integrating the AI contract as its first gate; result: passed in 143 seconds. It covered 20 AI harness checks, Django system check, 5 SSE/Celery contract tests, npm audit with 0 vulnerabilities, frontend lint, Vue compatibility inventory, production build, and authenticated Playwright smoke across series, project, prompts, and models pages.
- The first Redis/ASGI browser attempt exposed that `config.settings.development` ignored `SQLITE_DB_PATH`; the smoke now explicitly uses `config.settings.production`, which supports a disposable SQLite path.
- The second Redis/ASGI browser attempt reached the Django SSE view and Redis subscription but timed out before EventSource `open`. Local inspection of Django 3.2.15 proved its ASGI handler synchronously iterates streaming responses and blocks the event loop.
- Added `ProjectSSEASGIApplication`, its async contract tests, and the project SSE routing wrapper in `backend/config/asgi.py`.
- Ran `node scripts/validate-streaming-local.cjs --require-redis` against a temporary local Redis on 2026-07-13; result: passed in 58.8 seconds. Eight SSE/Celery/ASGI tests passed, Redis Pub/Sub passed, two SSE responses returned HTTP 200, non-terminal `done` kept both all-stage connections alive, and `pipeline_done`/`pipeline_error` reached the Vue UI and cleared recovery markers.
- Reran `node scripts/validate-local.cjs` after adding the ASGI adapter to the normal gate; result: passed in 82 seconds with 25 AI harness checks, 8 SSE/ASGI/Celery tests, npm audit at 0 vulnerabilities, lint, Vue inventory, production build, and the standard authenticated browser smoke.
- The first pull-request workflow runs failed during job setup because `astral-sh/setup-uv@v8` was not a resolvable tag. Both workflows now pin the official v8.1.0 commit `08807647e7069bb48b6ef5acd8ec9567f424441b`, and the AI harness enforces that pin.

## Next Action

Confirm the path-scoped `Streaming Redis Validation` pull-request run passes on Ubuntu, then consider a deterministic Celery worker + mock-provider pipeline smoke if worker orchestration becomes the next reliability bottleneck.
