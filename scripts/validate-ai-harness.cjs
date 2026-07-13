#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const failures = [];
let passed = 0;

function relative(filePath) {
  return path.relative(repoRoot, filePath).replaceAll('\\', '/');
}

function resolveRepo(relativePath) {
  return path.join(repoRoot, ...relativePath.split('/'));
}

function read(relativePath) {
  const filePath = resolveRepo(relativePath);
  if (!fs.existsSync(filePath)) {
    throw new Error(`missing required file: ${relativePath}`);
  }
  return fs.readFileSync(filePath, 'utf8');
}

function readJson(relativePath) {
  const source = read(relativePath);
  try {
    return JSON.parse(source);
  } catch (error) {
    throw new Error(`invalid JSON in ${relativePath}: ${error.message}`);
  }
}

function check(name, assertion, detail) {
  if (assertion) {
    passed += 1;
    console.log(`[ai-harness] ok: ${name}`);
    return;
  }

  failures.push(`${name}${detail ? ` - ${detail}` : ''}`);
  console.error(`[ai-harness] fail: ${failures.at(-1)}`);
}

function checkIncludes(name, source, expected) {
  const missing = expected.filter((item) => !source.includes(item));
  check(name, missing.length === 0, `missing: ${missing.join(', ')}`);
}

function extractAssignedBlock(source, assignment, closingPattern) {
  const pattern = new RegExp(`${assignment}\\s*=\\s*${closingPattern}`, 'm');
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`could not parse ${assignment}`);
  }
  return match[1];
}

function extractPythonStageList(relativePath, assignment) {
  const block = extractAssignedBlock(
    read(relativePath),
    assignment,
    '\\[([\\s\\S]*?)\\n\\s*\\]',
  );
  return [...block.matchAll(/['"]([a-z][a-z0-9_]*)['"]/g)].map((match) => match[1]);
}

function extractFrontendPromptStages() {
  const block = extractAssignedBlock(
    read('frontend/src/api/prompts.js'),
    'export const STAGE_TYPES',
    '\\[([\\s\\S]*?)\\n\\];',
  );
  return [...block.matchAll(/value:\s*['"]([a-z][a-z0-9_]*)['"]/g)].map((match) => match[1]);
}

function extractFrontendConstantStages() {
  const block = extractAssignedBlock(
    read('frontend/src/utils/constants.js'),
    'export const STAGE_TYPES',
    '\\{([\\s\\S]*?)\\n\\};',
  );
  return [...block.matchAll(/:\s*['"]([a-z][a-z0-9_]*)['"]/g)].map((match) => match[1]);
}

function extractPythonFunction(relativePath, functionName) {
  const source = read(relativePath);
  const pattern = new RegExp(
    `^def ${functionName}\\([^\\n]*\\):[\\s\\S]*?(?=^def |(?![\\s\\S]))`,
    'm',
  );
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`could not parse function ${functionName} in ${relativePath}`);
  }
  return match[0];
}

function compareStageSet(name, canonical, candidate) {
  const expected = [...new Set(canonical)].sort();
  const actual = [...new Set(candidate)].sort();
  const missing = expected.filter((stage) => !actual.includes(stage));
  const extra = actual.filter((stage) => !expected.includes(stage));
  const hasDuplicates = candidate.length !== new Set(candidate).size;

  check(
    name,
    missing.length === 0 && extra.length === 0 && !hasDuplicates,
    `missing=[${missing.join(', ')}] extra=[${extra.join(', ')}] duplicates=${hasDuplicates}`,
  );
}

function run() {
  const requiredFiles = [
    'AGENTS.md',
    '.codex/skills/ai-story-workspace/SKILL.md',
    'docs/project-map.md',
    'docs/done-definition.md',
    'docs/knowledge-graph.md',
    'docs/long-term-memory.md',
    'docs/automation-guardrails.md',
    'scripts/validate-local.cjs',
    'scripts/validate-streaming-local.cjs',
    'backend/apps/inference/models.py',
    'backend/apps/inference/services/errors.py',
    'backend/apps/inference/services/gates.py',
    'backend/apps/inference/services/hybrid.py',
    'backend/apps/inference/services/routing.py',
    'backend/apps/inference/services/scheduler.py',
    'backend/apps/inference/tests/test_postgres_concurrency.py',
    'backend/apps/inference/migrations/0004_seed_safe_local_ai_defaults.py',
    'backend/core/ai_client/runtime_agent_client.py',
    'backend/core/ai_client/outbound_guard.py',
    'backend/apps/projects/paid_safety.py',
    'runtime_agent/pyproject.toml',
    'runtime_agent/uv.lock',
    'runtime_agent/config.example.toml',
    'runtime_agent/runtime_agent/main.py',
    'runtime_agent/runtime_agent/journal.py',
    'runtime_agent/runtime_agent/process_supervisor.py',
    'runtime_agent/runtime_agent/registry.py',
    'runtime_agent/runtime_agent/scheduler.py',
    'scripts/local-ai/Test-LocalAIScripts.ps1',
    'scripts/local-ai/Install-RuntimeComponent.ps1',
    'scripts/local-ai/Install-ModelPack.ps1',
    'scripts/local-ai/components.example.json',
    'docs/local-ai/README.md',
    'docs/local-ai/IMPLEMENTATION_STATUS.md',
    'docs/local-ai/USER_GUIDE.md',
    'docs/local-ai/CONFIGURATION_GUIDE.md',
    'docs/local-ai/OPERATIONS_RUNBOOK.md',
    'docs/local-ai/COST_PRIVACY_GUIDE.md',
    'docs/local-ai/API_REFERENCE.md',
    'docs/local-ai/BENCHMARK_REPORT.md',
    'benchmarks/local-ai/README.md',
    'benchmarks/local-ai/manifest.json',
    'benchmarks/local-ai/text/chinese-storyboard.json',
    'benchmarks/local-ai/image-edit/character-consistency.json',
    'benchmarks/local-ai/video-motion/camera-motions.json',
    'backend/apps/projects/asgi_sse.py',
    'backend/apps/projects/tests/test_asgi_sse.py',
    'frontend/scripts/streaming-authenticated-smoke.cjs',
    '.github/workflows/validation.yml',
    '.github/workflows/streaming-redis-validation.yml',
  ];
  const missingFiles = requiredFiles.filter((file) => !fs.existsSync(resolveRepo(file)));
  check('required AI operating surfaces exist', missingFiles.length === 0, `missing: ${missingFiles.join(', ')}`);

  const canonicalStages = extractPythonStageList(
    'backend/apps/projects/utils.py',
    'PROJECT_STAGE_TYPES',
  );
  check('canonical stage list is non-empty and unique', canonicalStages.length > 0 && canonicalStages.length === new Set(canonicalStages).size);
  compareStageSet(
    'ProjectStage choices match canonical stage membership',
    canonicalStages,
    extractPythonStageList('backend/apps/projects/models.py', 'STAGE_TYPES'),
  );
  compareStageSet(
    'PromptTemplate choices match canonical stage membership',
    canonicalStages,
    extractPythonStageList('backend/apps/prompts/models.py', 'STAGE_TYPES'),
  );
  compareStageSet(
    'frontend prompt choices match canonical stage membership',
    canonicalStages,
    extractFrontendPromptStages(),
  );
  compareStageSet(
    'frontend constants match canonical stage membership',
    canonicalStages,
    extractFrontendConstantStages(),
  );

  const executionOrderFunction = extractPythonFunction(
    'backend/apps/projects/utils.py',
    'get_project_stage_order',
  );
  checkIncludes('execution-order function covers every canonical stage', executionOrderFunction, canonicalStages);

  const celeryTasks = read('backend/apps/projects/tasks.py');
  check(
    'project tasks use the canonical Celery app',
    /from\s+config\.celery_app\s+import\s+app/.test(celeryTasks),
    'expected `from config.celery_app import app`',
  );
  check(
    'legacy Celery module is absent',
    !fs.existsSync(resolveRepo('backend/config/celery.py')),
    'backend/config/celery.py conflicts with the documented source of truth',
  );

  const rootUrls = read('backend/config/urls.py');
  checkIncludes('optional agent/MCP routes stay guarded', rootUrls, [
    "optional_include('mcp/', 'apps.mcp.urls')",
    "optional_include('api/v1/agent/', 'apps.agent.urls')",
    'find_spec(module_path)',
  ]);

  checkIncludes('ASGI entrypoint installs the non-blocking SSE adapter', read('backend/config/asgi.py'), [
    'ProjectSSEASGIApplication',
    'django_application',
  ]);

  const frontendSSE = read('frontend/src/services/sseService.js');
  checkIncludes('frontend SSE URLs use the configured API base exactly once', frontendSSE, [
    "process.env.VUE_APP_API_BASE_URL || '/api/v1'",
    '${API_BASE_URL}/projects/sse/projects/',
  ]);
  check(
    'frontend SSE URLs do not duplicate the API prefix',
    !frontendSSE.includes('${API_BASE_URL}/api/v1/projects/'),
    'remove the duplicate `/api/v1` segment after API_BASE_URL',
  );

  const agents = read('AGENTS.md');
  checkIncludes('AGENTS routes future work through canonical surfaces', agents, [
    'docs/project-map.md',
    'docs/done-definition.md',
    'docs/knowledge-graph.md',
    'docs/long-term-memory.md',
    'docs/automation-guardrails.md',
    '.codex/skills/ai-story-workspace/SKILL.md',
    'scripts/validate-ai-harness.cjs',
  ]);

  const workspaceSkill = read('.codex/skills/ai-story-workspace/SKILL.md');
  checkIncludes('workspace skill loads and validates canonical surfaces', workspaceSkill, [
    'docs/project-map.md',
    'docs/done-definition.md',
    'docs/knowledge-graph.md',
    'docs/long-term-memory.md',
    'docs/automation-guardrails.md',
    'scripts/validate-ai-harness.cjs',
  ]);

  const projectMap = read('docs/project-map.md');
  checkIncludes('project map records every canonical stage', projectMap, canonicalStages);
  checkIncludes('project map records the AI harness gate', projectMap, ['scripts/validate-ai-harness.cjs']);
  checkIncludes('project map navigates the hybrid inference surfaces', projectMap, [
    'backend/apps/inference/',
    'runtime_agent/',
    'frontend/src/views/local-ai/',
    'scripts/local-ai/',
    'benchmarks/local-ai/',
    'AI_ROUTER_V2_ENABLED=false',
  ]);

  const knowledgeGraph = read('docs/knowledge-graph.md');
  checkIncludes('knowledge graph connects the harness to control surfaces', knowledgeGraph, [
    'Harness',
    'docs/project-map.md',
    'docs/done-definition.md',
    'docs/long-term-memory.md',
  ]);
  checkIncludes('knowledge graph records paid-call gates and durable execution ownership', knowledgeGraph, [
    'ProjectAISettings',
    'ProviderPriceRate',
    'BudgetReservation',
    'GenerationWorkItem',
    'MediaArtifact',
    'Runtime Agent',
    'Subjective quality',
  ]);

  const memory = read('docs/long-term-memory.md');
  checkIncludes('long-term memory satisfies the handoff contract', memory, [
    '## Facts',
    '## Decisions',
    '## Assumptions',
    '## Validation',
    '## Next Action',
  ]);
  checkIncludes('long-term memory records safe hybrid defaults and activation boundary', memory, [
    'backend/apps/inference/',
    'AI_ROUTER_V2_ENABLED=false',
    'budgets of `0`',
    'No target-machine benchmark result has been recorded yet',
  ]);

  const doneDefinition = read('docs/done-definition.md');
  checkIncludes('done definition includes hybrid, Agent, script, and hardware gates', doneDefinition, [
    'apps.inference.tests',
    'project cloud authorization',
    'effective versioned price',
    'budget reservation',
    'uv run --extra test pytest',
    'Test-LocalAIScripts.ps1',
    'benchmarks/local-ai/manifest.json',
    'PostgreSQL',
    'real Redis',
  ]);

  const paidGate = read('backend/apps/inference/services/gates.py');
  checkIncludes('paid-call gate remains fail-closed before external generation', paidGate, [
    'allow_cloud_data_transfer',
    'cloud_authorized_by_id',
    'PricingService.estimate',
    'project_budget_cny',
    'daily_limit_cny',
    'monthly_limit_cny',
    "status = 'manual_review'",
    '主观质量不满意',
  ]);

  const hybridExecution = read('backend/apps/inference/services/hybrid.py');
  checkIncludes('explicit paid execution requires one-shot confirmation and a cost cap', hybridExecution, [
    'PAID_CONFIRMATION_REQUIRED',
    'confirmed_max_cost_cny',
    '自动回退只能由 V2 路由触发',
  ]);
  checkIncludes('paid-only routes cannot become automatic primary routes', read('backend/apps/inference/services/routing.py'), [
    'has_configured_local',
    "target.role != 'paid_fallback'",
    '误建了只有 paid_fallback 的路由',
  ]);
  checkIncludes('legacy full-pipeline API bindings are checked before web and worker execution', read('backend/apps/projects/paid_safety.py'), [
    'pipeline_direct_api_providers',
    'PromptTemplate',
    'ProjectModelConfig',
    'fail closed',
  ]);

  const inferenceErrors = read('backend/apps/inference/services/errors.py');
  checkIncludes('automatic fallback is limited to explicit technical/contract codes', inferenceErrors, [
    'AUTO_FALLBACK_CODES',
    'NEVER_AUTO_PAY_CODES',
    'CLOUD_NOT_AUTHORIZED',
    'BUDGET_DENIED',
    'PRICE_MISSING',
    '审美和角色一致性等主观问题永远不会进入自动付费回退',
  ]);

  const safeSeed = read('backend/apps/inference/migrations/0004_seed_safe_local_ai_defaults.py');
  checkIncludes('safe seed keeps resources bounded, budgets zero, and real providers inactive', safeSeed, [
    "name='local-gpu-0'",
    "'resource_groups': {'gpu': 1}",
    "name='local-cpu-motion'",
    "'resource_groups': {'cpu_motion': 2}",
    "'daily_limit_cny': Decimal('0')",
    "'monthly_limit_cny': Decimal('0')",
    "'tooling_limit_cny': Decimal('0')",
    "'is_active': False",
  ]);
  checkIncludes('PostgreSQL gate covers 20-way budget and work-item contention', read('backend/apps/inference/tests/test_postgres_concurrency.py'), [
    "@skipUnlessDBFeature('has_select_for_update')",
    'ThreadPoolExecutor(max_workers=count)',
    "results.count('reserved'), 5",
    "results.count('claimed'), 1",
    'connections.close_all()',
  ]);

  const providerSerializer = read('backend/apps/models/serializers.py');
  const runtimeSerializer = read('backend/apps/inference/serializers.py');
  checkIncludes('provider and Runtime Agent secrets stay write-only/masked', providerSerializer, [
    "'api_key': {'write_only': True",
    'has_api_key',
    'api_key_masked',
  ]);
  checkIncludes('runtime-node token stays write-only/masked', runtimeSerializer, [
    'write_only=True',
    'has_access_token',
    'access_token_masked',
  ]);

  const runtimeAgent = read('runtime_agent/runtime_agent/main.py');
  checkIncludes('Runtime Agent exposes the authenticated v1 job/artifact/reload contract', runtimeAgent, [
    '@app.get("/v1/health/live"',
    '@app.get("/v1/health/ready"',
    '@app.post("/v1/jobs"',
    'alias="Idempotency-Key"',
    '@app.get("/v1/jobs/{job_id}"',
    '@app.delete(',
    '/v1/artifacts/{artifact_id}',
    '/v1/runtime-reloads',
  ]);
  checkIncludes('Runtime Agent keeps GPU and CPU-motion capacities explicit', read('runtime_agent/runtime_agent/config.py'), [
    'gpu_capacity: int = 1',
    'cpu_motion_capacity: int = 2',
    '"gpu": self.gpu_capacity',
    '"cpu_motion": self.cpu_motion_capacity',
  ]);
  checkIncludes('Runtime Agent configuration defaults to Mock and keeps real adapters disabled', read('runtime_agent/config.example.toml'), [
    'mode = "mock"',
    '[adapters.ollama]',
    '[adapters.comfyui_text2image]',
    '[adapters.lightx2v]',
    '[adapters.ffmpeg_motion]',
    'enabled = false',
  ]);
  checkIncludes('Runtime Agent supervises cancellable CLI process trees', read('runtime_agent/runtime_agent/process_supervisor.py'), [
    'CREATE_NEW_PROCESS_GROUP',
    'taskkill',
    'start_new_session',
    'SIGKILL',
    'EXECUTION_TIMEOUT',
  ]);

  const modelInstaller = read('scripts/local-ai/Install-ModelPack.ps1');
  checkIncludes('Windows model installer keeps confirmation, partial download, hash, and atomic switch', modelInstaller, [
    '.partial',
    'Get-FileHash',
    'SHA256',
    'Move-Item',
    'confirmation',
  ]);
  checkIncludes('Windows component installer is manifest driven and fail closed', read('scripts/local-ai/Install-RuntimeComponent.ps1'), [
    '.partial',
    'Assert-AIStoryApprovedDownloadPackage',
    'Save-AIStoryHttpFileWithResume',
    'Move-Item',
  ]);

  const localDocs = read('docs/local-ai/README.md');
  checkIncludes('local AI manual index covers the required operator documents', localDocs, [
    'USER_GUIDE.md',
    'WINDOWS_INSTALLATION.md',
    'CONFIGURATION_GUIDE.md',
    'OPERATIONS_RUNBOOK.md',
    'COST_PRIVACY_GUIDE.md',
    'MODEL_WORKFLOW_GUIDE.md',
    'TROUBLESHOOTING.md',
    'REMOTE_NODE_GUIDE.md',
    'BACKUP_AND_RECOVERY.md',
    'API_REFERENCE.md',
    'BENCHMARK_REPORT.md',
    'IMPLEMENTATION_STATUS.md',
  ]);

  checkIncludes('implementation status separates repository completion from real-machine evidence', read('docs/local-ai/IMPLEMENTATION_STATUS.md'), [
    '代码已实现',
    '默认关闭',
    '实机未验证',
    'PostgreSQL',
    'Redis',
    'PAID_PIPELINE_CONFIRMATION_REQUIRED',
  ]);

  const benchmarkManifest = readJson('benchmarks/local-ai/manifest.json');
  const benchmarkCategories = (benchmarkManifest.case_sets || []).map((item) => item.category).sort();
  check(
    'benchmark manifest is secret-free, unexecuted, and covers three required categories',
    benchmarkManifest.contains_secrets === false
      && benchmarkManifest.status === 'not_run'
      && JSON.stringify(benchmarkCategories) === JSON.stringify(['image_edit', 'text', 'video_motion']),
    `categories=${JSON.stringify(benchmarkCategories)} status=${benchmarkManifest.status}`,
  );
  for (const caseSet of benchmarkManifest.case_sets || []) {
    const caseData = readJson(caseSet.path);
    check(
      `${caseSet.category} benchmark remains secret-free and explicitly not run`,
      caseSet.status === 'not_run'
        && caseData.contains_secrets === false
        && caseData.status === 'not_run'
        && caseData.result?.status === 'not_run',
      `manifest=${caseSet.status} case=${caseData.status} result=${caseData.result?.status}`,
    );
  }

  const guardrails = read('docs/automation-guardrails.md');
  checkIncludes('automation guidance remains read-only and contract-shaped', guardrails, [
    'read-only',
    'scripts/validate-ai-harness.cjs',
    'Facts',
    'Checks Run / Checks Missing',
    'Risk',
    'Next Highest-Value Action',
  ]);

  const localValidation = read('scripts/validate-local.cjs');
  const ciValidation = read('.github/workflows/validation.yml');
  checkIncludes('local pre-PR gate runs the AI harness contract', localValidation, ['validate-ai-harness.cjs']);
  checkIncludes('CI gate runs the AI harness contract with pinned uv setup', ciValidation, [
    'validate-ai-harness.cjs',
    'astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b',
  ]);

  const streamingValidation = read('scripts/validate-streaming-local.cjs');
  const streamingWorkflow = read('.github/workflows/streaming-redis-validation.yml');
  checkIncludes('streaming gate runs ASGI contracts and browser smoke', streamingValidation, [
    'test_asgi_sse',
    'streaming-authenticated-smoke.cjs',
  ]);
  checkIncludes('streaming smoke isolates its disposable SQLite database from production settings', read('frontend/scripts/streaming-authenticated-smoke.cjs'), [
    "DJANGO_SETTINGS_MODULE: 'config.settings.development'",
    'SQLITE_DB_PATH: sqlitePath',
  ]);
  checkIncludes('streaming CI installs Playwright before the browser smoke', streamingWorkflow, [
    'pull_request:',
    'astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b',
    'npm ci',
    'playwright install --with-deps chromium',
    'validate-streaming-local.cjs --require-redis',
  ]);

  for (const smokeScript of [
    'frontend/scripts/authenticated-smoke.cjs',
    'frontend/scripts/streaming-authenticated-smoke.cjs',
  ]) {
    checkIncludes(`${smokeScript} cleans Linux process groups`, read(smokeScript), [
      'detached: !isWindows',
      "process.kill(-child.pid, 'SIGTERM')",
    ]);
  }

  check(
    'frontend runtime source-of-truth files remain Vue 3/Vuex 4',
    read('frontend/src/router/index.js').includes('createRouter')
      && read('frontend/src/store/index.js').includes('createStore'),
    'router/store source truth no longer matches the project map',
  );

  if (failures.length > 0) {
    console.error(`\n[ai-harness] ${failures.length} check(s) failed; ${passed} passed`);
    process.exit(1);
  }

  console.log(`\n[ai-harness] all ${passed} checks passed`);
}

try {
  run();
} catch (error) {
  console.error(`[ai-harness] fatal: ${error.message}`);
  process.exit(1);
}
