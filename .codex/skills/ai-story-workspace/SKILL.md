---
name: ai-story-workspace
description: Use for repo-scoped work in the AI Story Django/Vue workspace, especially workflow stages, prompt/model configuration, SSE/Celery generation, page assistant/OpenCode integration, UI consistency, validation, and documentation drift.
---

# AI Story Workspace

Use this repo-scoped skill whenever a task touches this workspace's code, docs, validation, or AI operating surface.

## Context Order

1. Read `AGENTS.md`.
2. Read `docs/project-map.md`.
3. Read `docs/done-definition.md`.
4. For domain relationships, read `docs/knowledge-graph.md`.
5. For restart state or known gaps, read `docs/long-term-memory.md`.
6. For recurring reviews or monitors, read `docs/automation-guardrails.md`.

Stop loading context once the next edit or answer is justified.

## Source Truth

- Prefer current source code over older implementation docs when they disagree.
- Treat `PROJECT_STAGE_TYPES` in `backend/apps/projects/utils.py`, `ProjectStage.STAGE_TYPES`, prompt choices, and frontend stage constants as stage-membership truth.
- Treat `get_project_stage_order()` in `backend/apps/projects/utils.py` as execution-order truth; presentation choice lists may use a different order.
- Treat `backend/config/celery_app.py` as Celery app truth.
- Treat `backend/apps/projects/asgi_sse.py` plus `backend/config/asgi.py` as ASGI streaming truth; Django 3.2's synchronous `StreamingHttpResponse` view remains the WSGI/test path.
- Treat `frontend/src/router/index.js` and `frontend/src/store/index.js` as frontend route/store truth.
- Keep `frontend/src/views/prompts/PromptList.vue` as the visual baseline for project/canvas UI changes.

## Optional Module Gate

Before page-assistant or MCP API validation, check whether these optional modules exist:

- `backend/apps/mcp/urls.py`
- `backend/apps/agent/urls.py`

The root URLConf should mount them only when `find_spec()` can locate them. `manage.py check` passing proves backend URLConf health, not optional page-assistant or MCP API behavior.

## Validation Route

- Rules, skills, project docs, stage membership, optional routes, or validation wiring:
  `node scripts/validate-ai-harness.cjs`
- Backend: choose the narrowest relevant `uv run python manage.py test ...`, `uv run python manage.py check`, or migration dry-run from `docs/done-definition.md`.
- Redis/ASGI/EventSource/Vue streaming: `node scripts/validate-streaming-local.cjs --require-redis` with a reachable Redis endpoint.
- Frontend: run `npm install` before lint/build if `frontend/node_modules` is absent; then use `npm run lint` or `npm run build`.
- Docs/control surface: run the workspace audit script when available:
  `python C:/Users/32674/.codex/skills/deep-execution-upgrade/scripts/audit_environment.py --mode workspace --workspace <repo> --format text`
- Handoff files: validate with:
  `python C:/Users/32674/.codex/skills/deep-execution-upgrade/scripts/validate_handoff_contract.py --path <markdown-file> --format text`

## Reporting

Close substantial work with:

- changed surfaces
- checks run
- checks missing
- residual risk
- next highest-value action

For automation-style reports, use only:

- `Facts`
- `Checks Run / Checks Missing`
- `Risk`
- `Next Highest-Value Action`
