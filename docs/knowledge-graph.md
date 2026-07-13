# Knowledge Graph

Last updated: 2026-07-13

## Core Domain Graph

```mermaid
flowchart LR
  User["User"] --> Series["Series"]
  Series --> Project["Project / Episode"]
  Project --> ProjectStage["ProjectStage"]
  Project --> ProjectModelConfig["ProjectModelConfig"]
  Project --> ProjectAISettings["ProjectAISettings"]
  Project --> GenerationRoute["GenerationRoute"]
  GenerationRoute --> GenerationTarget["GenerationTarget"]
  GenerationTarget --> ModelProvider
  GenerationTarget --> RuntimeNode["RuntimeNode"]
  Project --> GenerationWorkItem["GenerationWorkItem"]
  GenerationWorkItem --> MediaArtifact["MediaArtifact"]
  GenerationWorkItem --> BudgetReservation["BudgetReservation"]
  BudgetReservation --> ProviderPriceRate["ProviderPriceRate"]
  Project --> ProjectAssetBinding["ProjectAssetBinding"]
  Project --> ContentRewrite["ContentRewrite"]
  Project --> Storyboard["Storyboard"]
  Storyboard --> GeneratedImage["GeneratedImage"]
  Storyboard --> CameraMovement["CameraMovement"]
  Storyboard --> GeneratedVideo["GeneratedVideo"]
  Storyboard --> MultiGridTile["MultiGridTile"]
  Project --> MultiGridImageTask["MultiGridImageTask"]
  Project --> EditedImage["EditedImage"]
  Project --> PromptTemplateSet["PromptTemplateSet"]
  PromptTemplateSet --> PromptTemplate["PromptTemplate"]
  PromptTemplateSet --> GlobalVariable["GlobalVariable / Asset"]
  ProjectModelConfig --> ModelProvider["ModelProvider"]
  ModelProvider --> VendorConnectionConfig["VendorConnectionConfig"]
  ModelProvider --> ModelUsageLog["ModelUsageLog"]
  Project --> EpisodeTaskQueue["EpisodeTaskQueue"]
  Screenplay["Screenplay"] --> ScreenplayEpisode["ScreenplayEpisode"]
  Screenplay --> Project
```

## Execution Graph

```mermaid
flowchart TB
  UI["Vue views and canvas nodes"] --> API["frontend/src/api and services"]
  API --> DRF["Django REST ViewSets / APIViews"]
  DRF --> Queue["apps.projects.queue_service"]
  DRF --> Tasks["apps.projects.tasks"]
  Tasks --> Redis["RedisStreamPublisher"]
  Redis --> SSE["ProjectStageSSEView (WSGI) / ProjectSSEASGIApplication (ASGI)"]
  SSE --> EventSource["frontend/src/services/sseService.js"]
  Tasks --> Processors["apps.content.processors"]
  Processors --> AIClients["core.ai_client executors"]
  AIClients --> Providers["ModelProvider / external AI APIs"]
  Tasks --> Media["storage / media outputs"]
```

## Hybrid Inference Graph

```mermaid
flowchart LR
  Request["Project generation request"] --> Router["RoutingService / HybridInferenceService"]
  Router --> LocalTargets["local_primary / local_secondary"]
  LocalTargets --> AgentClient["RuntimeAgentClient"]
  AgentClient --> Agent["FastAPI Runtime Agent :9100"]
  Agent --> Journal["Agent SQLite WAL journal"]
  Agent --> GPU["gpu resource group capacity 1"]
  Agent --> Motion["cpu_motion capacity 2"]
  GPU --> Ollama["Ollama adapter"]
  GPU --> Comfy["ComfyUI adapter"]
  GPU --> Light["LightX2V adapter"]
  Motion --> FFmpeg["FFmpeg motion adapter"]

  LocalTargets -->|"technical/contract failure only"| CloudAuth["Project cloud authorization"]
  CloudAuth --> Price["Effective ProviderPriceRate"]
  Price --> Budget["Project + daily + monthly reservation"]
  Budget --> Paid["paid_fallback Provider"]
  Paid --> Ledger["ModelUsageLog + BudgetReservation"]
  Agent --> Artifact["MediaArtifact"]
  Paid --> Artifact
```

The cloud edge is conjunctive and ordered: authorization, price, and budget must all pass before an external generation POST. Missing configuration fails closed. Subjective quality dissatisfaction has no automatic edge to `paid_fallback`; the UI must create an explicit, estimated API-regeneration request.

## Repo Control Graph

```mermaid
flowchart LR
  Agents["AGENTS.md"] --> ProjectMap["docs/project-map.md"]
  Agents --> Done["docs/done-definition.md"]
  Agents --> Skill[".codex/skills/ai-story-workspace/SKILL.md"]
  Harness["scripts/validate-ai-harness.cjs"] --> Agents
  Harness --> ProjectMap
  Harness --> Done
  Harness --> Knowledge["docs/knowledge-graph.md"]
  Harness --> Memory["docs/long-term-memory.md"]
  Harness --> Automation["docs/automation-guardrails.md"]
  Harness --> LocalDocs["docs/local-ai/"]
  Harness --> Benchmarks["benchmarks/local-ai/"]
  Harness --> RuntimeAgent["runtime_agent/"]
  Harness --> WindowsScripts["scripts/local-ai/"]
  ProjectMap --> Knowledge["docs/knowledge-graph.md"]
  ProjectMap --> Memory["docs/long-term-memory.md"]
  Done --> Automation["docs/automation-guardrails.md"]
  Skill --> ProjectMap
  Skill --> Done
  Skill --> Memory
```

## Important Edges

- `ProjectStage.stage_type` membership must stay aligned with `PROJECT_STAGE_TYPES` in `backend/apps/projects/utils.py`, frontend stage constants, prompt template stage types, and task dispatch branches.
- `get_project_stage_order()` is execution-order truth. Do not require presentation choice lists to have the same order when their stage membership is identical.
- `PromptTemplateSet` controls which stages are enabled; disabled stage templates can cause tasks to skip stages intentionally.
- `multi_grid_image` and `image_edit` are advanced image flows. When either is enabled, `image_generation` is normalized off by `normalize_stage_template_states`.
- SSE channel names follow `ai_story:project:{project_id}:stage:{stage_name}` in Redis publisher/subscriber code.
- Under ASGI, `backend/config/asgi.py` routes the two project SSE endpoints through `ProjectSSEASGIApplication`; all other HTTP traffic continues to Django.
- All-stage EventSource connections must survive `done` and terminate only on `pipeline_done` or `pipeline_error`; the browser smoke verifies both terminal branches reach Vue UI.
- Vue Router requires auth on core routes; API clients should assume JWT/session auth unless an endpoint explicitly allows anonymous access.
- Page assistant/OpenCode integration crosses `frontend/src/services/pageAgent/`, `docs/agent_api.md`, `backend/config/settings/base.py`, and the optional `apps.agent` route surface.
- `ProjectAISettings` owns per-project quality, cloud-data authorization, and project budget. Cloud authorization must record actor/time and cannot be inferred from choosing an API Provider.
- `GenerationRoute` resolves ordered local/API targets; the paid edge is guarded by `ProviderPriceRate` and `BudgetReservation`, never by a provider URL heuristic.
- `GenerationWorkItem` is the durable business state machine (`waiting/leased/running/retry_wait/succeeded/failed/cancelled`). Runtime Agent job state is node-local and cannot replace the Django work-item record.
- Redis resource leases serialize GPU work across local providers; Agent resource semaphores provide a second node-local capacity boundary. Owner-checked TTL renewal/release prevents one worker from unlocking another.
- `MediaArtifact` holds storage-neutral hashes/metadata and lifecycle (`source/intermediate/failed/final`). GeneratedImage/EditedImage/GeneratedVideo remain compatible content records linked to it.
- Runtime Agent never reads the Django business database and never holds paid Provider keys. Its Bearer token protects non-loopback requests; remote nodes additionally require TLS.
- `benchmarks/local-ai/` fixes secret-free inputs/seeds. `docs/local-ai/BENCHMARK_REPORT.md` remains “not run” until target-machine model/workflow hashes and measurements exist.

## Drift Watchlist

- `apps.agent` and `apps.mcp` route modules are optional; the root URLConf mounts them only when source or compiled modules are present.
- `config/celery_app.py` is the current Celery app path; reject future drift back to `config/celery.py`.
- Some historical docs mark frontend prompt pages as incomplete even though current source includes prompt views and store modules.
- API docs in feature guides may use old line counts and endpoint assumptions; verify against current router and ViewSets.
- Seeded local Providers are intentionally inactive and budgets are `0`. Mock Agent/API contract success must not be interpreted as real Ollama/FLUX/Wan availability.
- PostgreSQL is the production truth for row-lock budget and work-item concurrency; SQLite remains the development and single-process contract path.
