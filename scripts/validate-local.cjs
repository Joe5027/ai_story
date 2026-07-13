#!/usr/bin/env node

const { spawnSync } = require('child_process');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const isWindows = process.platform === 'win32';
const npmCommand = isWindows ? 'cmd.exe' : 'npm';
const npmArgs = (args) => (isWindows ? ['/d', '/s', '/c', 'npm.cmd', ...args] : args);

const checks = [
  {
    name: 'AI workspace contract',
    cwd: repoRoot,
    command: 'node',
    args: ['scripts/validate-ai-harness.cjs'],
  },
  {
    name: 'Backend Django check',
    cwd: path.join(repoRoot, 'backend'),
    command: 'uv',
    args: ['run', 'python', 'manage.py', 'check'],
  },
  {
    name: 'Backend SSE, ASGI, and Celery contract tests',
    cwd: path.join(repoRoot, 'backend'),
    command: 'uv',
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
    name: 'Frontend npm audit',
    cwd: path.join(repoRoot, 'frontend'),
    command: npmCommand,
    args: npmArgs(['audit', '--audit-level=low']),
  },
  {
    name: 'Frontend lint',
    cwd: path.join(repoRoot, 'frontend'),
    command: npmCommand,
    args: npmArgs(['run', 'lint']),
  },
  {
    name: 'Vue 2 compatibility audit',
    cwd: path.join(repoRoot, 'frontend'),
    command: npmCommand,
    args: npmArgs(['run', 'audit:vue2']),
  },
  {
    name: 'Frontend production build',
    cwd: path.join(repoRoot, 'frontend'),
    command: npmCommand,
    args: npmArgs(['run', 'build']),
  },
  {
    name: 'Authenticated browser smoke',
    cwd: path.join(repoRoot, 'frontend'),
    command: npmCommand,
    args: npmArgs(['run', 'smoke:auth']),
  },
];

function runCheck(check) {
  console.log(`\n[validate] ${check.name}`);
  console.log(`[validate] ${check.command} ${check.args.join(' ')}`);

  const result = spawnSync(check.command, check.args, {
    cwd: check.cwd,
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  });

  if (result.error) {
    console.error(`[validate] failed to start ${check.name}: ${result.error.message}`);
    process.exit(result.error.code === 'ENOENT' ? 127 : 1);
  }

  if (result.status !== 0) {
    console.error(`[validate] failed: ${check.name}`);
    process.exit(result.status || 1);
  }
}

for (const check of checks) {
  runCheck(check);
}

console.log('\n[validate] all checks passed');
