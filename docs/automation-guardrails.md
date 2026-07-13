# Automation Guardrails

Last updated: 2026-07-13

## Purpose

Use this file when creating recurring reviews, monitors, reminders, or automation prompts for this repository. Default automation must be read-only and should produce one highest-value recommendation, not a backlog.

## Hard Rules

- Default to read-only review. Do not edit tracked files, run migrations, delete media, rotate credentials, create commits, push branches, or call external write APIs unless the user explicitly asks.
- Do not inspect or print secrets from `.env`, model-provider records, JWT storage, OpenCode passwords, or API-key fields.
- Do not traverse generated media or dependency trees unless the prompt is specifically about those paths. Exclude `storage/`, `frontend/node_modules/`, `backend/media/`, `.git/`, and build output by default.
- If backend startup is being reviewed, check whether optional `apps.agent` / `apps.mcp` modules are present and whether `backend/config/urls.py` still mounts them conditionally.
- If frontend lint/build is being reviewed, first check whether `frontend/node_modules` exists.
- If runtime truth matters, prefer current session/tool evidence over configured inventory.

## Required Output Sections

Every recurring automation output should use exactly these top-level sections:

- `Facts`
- `Checks Run / Checks Missing`
- `Risk`
- `Next Highest-Value Action`

## Workspace Harness Review Prompt

```text
Review the AI Story workspace read-only.

Start from:
- AGENTS.md
- docs/project-map.md
- docs/done-definition.md
- docs/long-term-memory.md
- .codex/skills/ai-story-workspace/SKILL.md

Run first:
- node scripts/validate-ai-harness.cjs

Check for:
- source/docs drift in route, stage, Celery, SSE, prompt, model, and frontend module guidance
- missing validation gates after recent source changes; treat a failed AI harness contract as the primary fact and report the exact failed invariant
- stale references to hard-required apps.agent.urls/apps.mcp.urls, config/celery.py, or ProjectDetailNew.vue
- automation or docs that imply write access without explicit user approval

Do not edit files. Do not inspect secrets. Exclude generated media, node_modules, .git, and build outputs.

Report only:
Facts
Checks Run / Checks Missing
Risk
Next Highest-Value Action
```

## Backend Health Review Prompt

```text
Review backend health read-only.

Check:
- whether backend/apps/agent/urls.py and backend/apps/mcp/urls.py are either present or conditionally skipped by backend/config/urls.py
- whether Celery docs refer to config/celery_app.py, not config/celery.py
- whether stage constants stay aligned across ProjectStage, backend/apps/projects/utils.py, task dispatch, prompt template stages, and frontend stage constants
- the narrowest test command that should be run for any changed backend area

Do not run migrations. Do not change files. Do not print secrets.

Report only:
Facts
Checks Run / Checks Missing
Risk
Next Highest-Value Action
```

## Streaming Health Review Prompt

```text
Review AI Story streaming health read-only.

Check:
- whether SSE endpoints in backend/apps/projects/sse_views.py still terminate single-stage streams on done/error and all-stage streams only on pipeline_done/pipeline_error
- whether backend/config/asgi.py still routes project SSE paths through backend/apps/projects/asgi_sse.py instead of Django 3.2's blocking ASGI StreamingHttpResponse iteration
- whether frontend/src/services/sseService.js uses matching EventSource URLs and terminal event handling
- whether Celery task registration still points to config.celery_app.app
- whether Redis settings still keep Celery broker /0, Celery result /1, Pub/Sub /2, and cache /4 separate
- whether the narrowest local validation should be node scripts/validate-streaming-local.cjs or node scripts/validate-streaming-local.cjs --require-redis; the required form must include the authenticated Daphne/EventSource/Vue browser smoke

Do not start workers, mutate queues, inspect secrets, or edit files.

Report only:
Facts
Checks Run / Checks Missing
Risk
Next Highest-Value Action
```

## Frontend UI Review Prompt

```text
Review frontend UI consistency read-only.

Check:
- project and canvas UI changes against frontend/src/views/prompts/PromptList.vue visual language
- text overflow, button sizing, and dark-theme compatibility
- whether npm dependencies are installed before recommending lint/build conclusions
- route/API/store consistency for changed views

Do not edit files. Do not launch external browser automation unless explicitly requested.

Report only:
Facts
Checks Run / Checks Missing
Risk
Next Highest-Value Action
```

## Escalation Rules

- If an automation finds a direct, high-confidence, low-risk fix, route it to a manual `deep-execution-upgrade` or coding task; do not auto-edit.
- If an automation run is blocked by dependencies or missing generated/private source, state the exact missing condition and stop.
- If a run is clean, keep the report short and archive/suppress only when the configured automation explicitly supports anomaly-only reporting.
