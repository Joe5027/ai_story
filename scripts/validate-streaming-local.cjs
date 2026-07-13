#!/usr/bin/env node

const { spawnSync } = require('child_process');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const backendRoot = path.join(repoRoot, 'backend');
const frontendRoot = path.join(repoRoot, 'frontend');
const requireRedis = process.argv.includes('--require-redis') || process.env.REQUIRE_REDIS === '1';

const checks = [
  {
    name: 'SSE and Celery contract tests',
    command: 'uv',
    cwd: backendRoot,
    args: [
      'run',
      'python',
      'manage.py',
      'test',
      'apps.projects.tests.test_celery_contract',
      'apps.projects.tests.test_sse_views',
      'apps.projects.tests.test_asgi_sse',
    ],
  },
  {
    name: requireRedis
      ? 'Redis Pub/Sub smoke (Redis required)'
      : 'Redis Pub/Sub smoke (skips when Redis is unavailable)',
    command: 'uv',
    cwd: backendRoot,
    args: [
      'run',
      'python',
      'manage.py',
      'test',
      'apps.projects.tests.test_redis_pubsub',
    ],
    env: {
      ...process.env,
      REQUIRE_REDIS: requireRedis ? '1' : process.env.REQUIRE_REDIS || '',
    },
  },
  {
    name: requireRedis
      ? 'Redis + ASGI + EventSource browser smoke (Redis required)'
      : 'Redis + ASGI + EventSource browser smoke (skips when Redis is unavailable)',
    command: 'node',
    cwd: frontendRoot,
    args: ['scripts/streaming-authenticated-smoke.cjs'],
    env: {
      ...process.env,
      REQUIRE_REDIS: requireRedis ? '1' : process.env.REQUIRE_REDIS || '',
    },
  },
];

function runCheck(check) {
  console.log(`\n[streaming-validate] ${check.name}`);
  console.log(`[streaming-validate] ${check.command} ${check.args.join(' ')}`);

  const result = spawnSync(check.command, check.args, {
    cwd: check.cwd,
    env: check.env || process.env,
    stdio: 'inherit',
    windowsHide: true,
  });

  if (result.error) {
    console.error(`[streaming-validate] failed to start ${check.name}: ${result.error.message}`);
    process.exit(result.error.code === 'ENOENT' ? 127 : 1);
  }

  if (result.status !== 0) {
    console.error(`[streaming-validate] failed: ${check.name}`);
    process.exit(result.status || 1);
  }
}

for (const check of checks) {
  runCheck(check);
}

console.log('\n[streaming-validate] all checks passed');
