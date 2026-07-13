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

  const knowledgeGraph = read('docs/knowledge-graph.md');
  checkIncludes('knowledge graph connects the harness to control surfaces', knowledgeGraph, [
    'Harness',
    'docs/project-map.md',
    'docs/done-definition.md',
    'docs/long-term-memory.md',
  ]);

  const memory = read('docs/long-term-memory.md');
  checkIncludes('long-term memory satisfies the handoff contract', memory, [
    '## Facts',
    '## Decisions',
    '## Assumptions',
    '## Validation',
    '## Next Action',
  ]);

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
  checkIncludes('CI gate runs the AI harness contract', ciValidation, ['validate-ai-harness.cjs']);

  const streamingValidation = read('scripts/validate-streaming-local.cjs');
  const streamingWorkflow = read('.github/workflows/streaming-redis-validation.yml');
  checkIncludes('streaming gate runs ASGI contracts and browser smoke', streamingValidation, [
    'test_asgi_sse',
    'streaming-authenticated-smoke.cjs',
  ]);
  checkIncludes('streaming CI installs Playwright before the browser smoke', streamingWorkflow, [
    'pull_request:',
    'npm ci',
    'playwright install --with-deps chromium',
    'validate-streaming-local.cjs --require-redis',
  ]);

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
