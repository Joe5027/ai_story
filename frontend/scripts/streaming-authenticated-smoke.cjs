#!/usr/bin/env node

const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const { chromium } = require('playwright');

const isWindows = process.platform === 'win32';
const repoRoot = path.resolve(__dirname, '..', '..');
const backendDir = path.join(repoRoot, 'backend');
const frontendDir = path.join(repoRoot, 'frontend');
const smokeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-story-streaming-smoke-'));
const sqlitePath = path.join(smokeRoot, 'streaming.sqlite3');
const backendLogPath = path.join(smokeRoot, 'daphne.log');
const frontendLogPath = path.join(smokeRoot, 'frontend.log');

const npmCommand = isWindows ? 'cmd.exe' : 'npm';
const uvCommand = 'uv';
const host = process.env.SMOKE_HOST || '127.0.0.1';
const username = process.env.SMOKE_USERNAME || 'codex_streaming_smoke';
const password = process.env.SMOKE_PASSWORD || 'CodexStreaming123!';
const keepArtifacts = process.env.SMOKE_KEEP_ARTIFACTS === '1';
const requireRedis = process.env.REQUIRE_REDIS === '1';
const timeoutMs = Number(process.env.SMOKE_TIMEOUT_MS || 120000);
const redisUrl = new URL(
  process.env.REDIS_PUBSUB_URL
    || `redis://${process.env.REDIS_HOST || '127.0.0.1'}:${process.env.REDIS_PORT || '6379'}/2`,
);
const redisHost = redisUrl.hostname;
const redisPort = Number(redisUrl.port || 6379);

let backendProcess = null;
let frontendProcess = null;
let browser = null;
let cleaned = false;

function info(message) {
  console.log(`[smoke:streaming] ${message}`);
}

function tail(filePath, maxChars = 5000) {
  if (!fs.existsSync(filePath)) {
    return '';
  }
  return fs.readFileSync(filePath, 'utf8').slice(-maxChars);
}

function commandOutput(result) {
  return [
    result.stdout ? `stdout:\n${result.stdout}` : '',
    result.stderr ? `stderr:\n${result.stderr}` : '',
  ].filter(Boolean).join('\n');
}

function runChecked(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: {
      ...process.env,
      ...options.env,
    },
    encoding: 'utf8',
    timeout: options.timeout || timeoutMs,
    windowsHide: true,
  });

  if (result.error) {
    throw new Error(`Command failed to start: ${command} ${args.join(' ')}\n${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(' ')}\n${commandOutput(result)}`);
  }
  return result;
}

async function findFreePort(startPort) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.on('error', () => resolve(findFreePort(startPort + 1)));
    server.listen({ host, port: startPort }, () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function canConnect(targetHost, targetPort) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: targetHost, port: targetPort });
    const finish = (value) => {
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(1500);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

async function waitForHttp(url, expectedStatuses, label) {
  const startedAt = Date.now();
  let lastError = '';

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await new Promise((resolve, reject) => {
        const request = http.get(url, { timeout: 5000 }, resolve);
        request.on('timeout', () => request.destroy(new Error('request timeout')));
        request.on('error', reject);
      });
      response.resume();
      if (expectedStatuses.includes(response.statusCode)) {
        return;
      }
      lastError = `status ${response.statusCode}`;
    } catch (error) {
      lastError = error.message;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(`Timed out waiting for ${label} at ${url}: ${lastError}`);
}

function startProcess(command, args, options) {
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: {
      ...process.env,
      ...options.env,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const logStream = fs.createWriteStream(options.logPath, { flags: 'a' });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.on('close', (code, signal) => {
    logStream.end(`\n[process exit] code=${code} signal=${signal}\n`);
  });
  child.on('error', (error) => {
    logStream.write(`\n[process error] ${error.stack || error.message}\n`);
  });
  return child;
}

function stopProcess(child) {
  if (!child || child.killed || !child.pid) {
    return;
  }
  if (isWindows) {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true,
    });
    return;
  }
  try {
    child.kill('SIGTERM');
  } catch (_) {
    // Process may already be gone.
  }
}

async function cleanup() {
  if (cleaned) {
    return;
  }
  cleaned = true;
  if (browser) {
    try {
      await browser.close();
    } catch (_) {
      // Ignore browser cleanup errors.
    }
  }
  stopProcess(frontendProcess);
  stopProcess(backendProcess);

  if (keepArtifacts) {
    info(`kept artifacts at ${smokeRoot}`);
  } else {
    fs.rmSync(smokeRoot, { recursive: true, force: true });
  }
}

function installSignalHandlers() {
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, async () => {
      await cleanup();
      process.exit(signal === 'SIGINT' ? 130 : 143);
    });
  }
}

function buildDjangoEnv() {
  return {
    DJANGO_SETTINGS_MODULE: 'config.settings.production',
    SQLITE_DB_PATH: sqlitePath,
    REDIS_HOST: redisHost,
    REDIS_PORT: String(redisPort),
    REDIS_PUBSUB_URL: redisUrl.toString(),
    PYTHONIOENCODING: 'utf-8',
  };
}

function seedData(djangoEnv) {
  const seedCode = `
from django.contrib.auth import get_user_model
from apps.projects.models import Series
from apps.projects.serializers import create_project_with_resources

username = ${JSON.stringify(username)}
password = ${JSON.stringify(password)}
User = get_user_model()
user, _ = User.objects.update_or_create(
    username=username,
    defaults={'email': 'codex-streaming@example.local', 'is_active': True},
)
user.set_password(password)
user.save()

Series.objects.filter(user=user, name='Codex Streaming Series').delete()
series = Series.objects.create(
    user=user,
    name='Codex Streaming Series',
    description='Temporary series for Redis ASGI EventSource validation.',
)

def create_streaming_project(name, episode_number):
    project = create_project_with_resources({
        'series': series,
        'name': name,
        'episode_number': episode_number,
        'episode_title': name,
        'sort_order': episode_number,
        'description': 'Temporary streaming browser smoke project.',
        'original_topic': 'Validate Redis to ASGI to EventSource to Vue UI.',
    }, user)
    project.status = 'processing'
    project.save(update_fields=['status'])
    return project

success_project = create_streaming_project('Codex Streaming Success', 1)
error_project = create_streaming_project('Codex Streaming Error', 2)
print('SMOKE_SUCCESS_PROJECT_ID=' + str(success_project.id))
print('SMOKE_ERROR_PROJECT_ID=' + str(error_project.id))
`;

  const result = runChecked(uvCommand, ['run', 'python', 'manage.py', 'shell', '-c', seedCode], {
    cwd: backendDir,
    env: djangoEnv,
  });
  const successMatch = result.stdout.match(/SMOKE_SUCCESS_PROJECT_ID=([0-9a-f-]+)/i);
  const errorMatch = result.stdout.match(/SMOKE_ERROR_PROJECT_ID=([0-9a-f-]+)/i);
  if (!successMatch || !errorMatch) {
    throw new Error(`Seed command did not return both project ids.\n${commandOutput(result)}`);
  }
  return {
    successProjectId: successMatch[1],
    errorProjectId: errorMatch[1],
  };
}

function publishIntermediate(projectId, djangoEnv) {
  const code = `
from core.redis import RedisStreamPublisher
publisher = RedisStreamPublisher(${JSON.stringify(projectId)}, 'rewrite')
try:
    results = [
        publisher.publish_stage_update(status='processing', progress=40, message='Codex streaming update'),
        publisher.publish_done(metadata={'source': 'streaming-browser-smoke'}),
    ]
    assert all(results), results
finally:
    publisher.close()
`;
  runChecked(uvCommand, ['run', 'python', 'manage.py', 'shell', '-c', code], {
    cwd: backendDir,
    env: djangoEnv,
  });
}

function publishTerminal(projectId, outcome, djangoEnv) {
  const isSuccess = outcome === 'success';
  const projectStatus = isSuccess ? 'completed' : 'failed';
  const stageStatus = isSuccess ? 'completed' : 'failed';
  const terminalCall = isSuccess
    ? "publisher.publish_pipeline_done(metadata={'source': 'streaming-browser-smoke'})"
    : "publisher.publish_pipeline_error('Codex streaming pipeline error', metadata={'source': 'streaming-browser-smoke'})";
  const code = `
from apps.projects.models import Project, ProjectStage
from core.redis import RedisStreamPublisher
project_id = ${JSON.stringify(projectId)}
Project.objects.filter(id=project_id).update(status=${JSON.stringify(projectStatus)})
ProjectStage.objects.filter(project_id=project_id).update(status=${JSON.stringify(stageStatus)})
publisher = RedisStreamPublisher(project_id, 'rewrite')
try:
    assert ${terminalCall}
finally:
    publisher.close()
`;
  runChecked(uvCommand, ['run', 'python', 'manage.py', 'shell', '-c', code], {
    cwd: backendDir,
    env: djangoEnv,
  });
}

async function launchBrowser() {
  const attempts = process.env.PLAYWRIGHT_CHANNEL
    ? [{ channel: process.env.PLAYWRIGHT_CHANNEL }]
    : [{}, { channel: 'msedge' }, { channel: 'chrome' }];
  const errors = [];
  for (const attempt of attempts) {
    try {
      return await chromium.launch({
        ...attempt,
        headless: process.env.SMOKE_HEADLESS !== '0',
      });
    } catch (error) {
      errors.push(`${attempt.channel || 'bundled chromium'}: ${error.message}`);
    }
  }
  throw new Error(
    `Unable to launch a Playwright browser.\n${errors.join('\n')}\n`
      + 'Run `npx playwright install chromium` or set PLAYWRIGHT_CHANNEL=msedge/chrome.',
  );
}

async function waitForConsole(consoleLines, startIndex, pattern, label) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (consoleLines.slice(startIndex).some((line) => pattern.test(line.text))) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for browser console event: ${label}`);
}

async function waitForMarker(page, projectId, expectedValue) {
  const key = `project_active_pipeline_sse:${projectId}`;
  await page.waitForFunction(
    ({ storageKey, expected }) => sessionStorage.getItem(storageKey) === expected,
    { storageKey: key, expected: expectedValue },
    { timeout: 30000 },
  );
}

async function assertPipelineFlow(page, appBaseUrl, projectId, projectName, outcome, consoleLines, djangoEnv) {
  const consoleStart = consoleLines.length;
  await page.goto(`${appBaseUrl}/projects/${projectId}`);
  await page.getByText(projectName).first().waitFor({ timeout: 30000 });
  await waitForConsole(consoleLines, consoleStart, /\[Pipeline SSE\] 已连接:/, `${projectName} connected`);
  await waitForMarker(page, projectId, '1');

  publishIntermediate(projectId, djangoEnv);
  await page.getByText('剧本精修: Codex streaming update').first().waitFor({ timeout: 30000 });
  await waitForConsole(consoleLines, consoleStart, /\[Pipeline SSE\] done 消息:/, `${projectName} non-terminal done`);
  const markerAfterDone = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    `project_active_pipeline_sse:${projectId}`,
  );
  if (markerAfterDone !== '1') {
    throw new Error(`${projectName}: all-stage SSE closed on non-terminal done`);
  }

  publishTerminal(projectId, outcome, djangoEnv);
  const terminalText = outcome === 'success'
    ? '工作流执行完成！'
    : 'Codex streaming pipeline error';
  await page.getByText(terminalText, { exact: true }).first().waitFor({ timeout: 30000 });
  await waitForMarker(page, projectId, null);
  await waitForConsole(
    consoleLines,
    consoleStart,
    outcome === 'success' ? /\[Pipeline SSE\] 流程完成:/ : /\[Pipeline SSE\] 流程错误:/,
    `${projectName} terminal event`,
  );

  return {
    projectId,
    projectName,
    outcome,
    nonTerminalDoneKeptConnection: true,
    terminalClearedRecoveryMarker: true,
    terminalUiText: terminalText,
  };
}

async function runBrowserSmoke(appBaseUrl, projectIds, djangoEnv) {
  browser = await launchBrowser();
  const context = await browser.newContext({
    baseURL: appBaseUrl,
    viewport: { width: 1280, height: 720 },
  });
  const page = await context.newPage();
  const consoleLines = [];
  const pageErrors = [];
  const networkErrors = [];
  const sseResponses = [];

  page.on('console', (message) => {
    consoleLines.push({ type: message.type(), text: message.text() });
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => {
    if (/\/projects\/sse\/projects\//.test(response.url())) {
      sseResponses.push({ status: response.status(), url: response.url() });
    } else if (response.status() >= 400) {
      networkErrors.push({ status: response.status(), url: response.url() });
    }
  });

  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');
  if (await page.locator('input[type="text"]').count()) {
    await page.locator('input[type="text"]').fill(username);
    await page.locator('input[type="password"]').fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL((url) => url.pathname === '/series', { timeout: 30000 });
  }

  const flows = [];
  flows.push(await assertPipelineFlow(
    page,
    appBaseUrl,
    projectIds.successProjectId,
    'Codex Streaming Success',
    'success',
    consoleLines,
    djangoEnv,
  ));
  flows.push(await assertPipelineFlow(
    page,
    appBaseUrl,
    projectIds.errorProjectId,
    'Codex Streaming Error',
    'error',
    consoleLines,
    djangoEnv,
  ));

  await page.waitForTimeout(500);
  const relevantNetworkErrors = networkErrors.filter((entry) => {
    return !(entry.status === 404 && /\/api\/v1\/agent\//.test(entry.url));
  });
  const unexpectedConsoleErrors = consoleLines.filter((entry) => {
    if (!['error', 'warning'].includes(entry.type)) {
      return false;
    }
    return !/\[Pipeline SSE\] 流程错误:|\[SSE#\d+\] 连接错误:|Failed to load resource:/i.test(entry.text);
  });

  if (pageErrors.length) {
    throw new Error(`Browser page errors:\n${JSON.stringify(pageErrors, null, 2)}`);
  }
  if (relevantNetworkErrors.length) {
    throw new Error(`Browser network failures:\n${JSON.stringify(relevantNetworkErrors, null, 2)}`);
  }
  if (unexpectedConsoleErrors.length) {
    throw new Error(`Unexpected browser console errors:\n${JSON.stringify(unexpectedConsoleErrors, null, 2)}`);
  }
  if (sseResponses.length < 2 || sseResponses.some((entry) => entry.status !== 200)) {
    throw new Error(`Expected two successful SSE responses:\n${JSON.stringify(sseResponses, null, 2)}`);
  }

  await context.close();
  await browser.close();
  browser = null;
  return { flows, sseResponses };
}

async function main() {
  installSignalHandlers();

  if (!(await canConnect(redisHost, redisPort))) {
    const message = `Redis unavailable at ${redisHost}:${redisPort}`;
    if (requireRedis) {
      throw new Error(message);
    }
    info(`skipped: ${message}`);
    console.log(JSON.stringify({ ok: true, skipped: true, reason: message }, null, 2));
    return;
  }

  const backendPort = Number(process.env.BACKEND_PORT || await findFreePort(8010));
  const frontendPort = Number(process.env.FRONTEND_PORT || await findFreePort(13020));
  const appBaseUrl = process.env.SMOKE_APP_URL || `http://${host}:${frontendPort}`;
  const djangoEnv = buildDjangoEnv();

  info(`Redis reachable at ${redisHost}:${redisPort}`);
  info(`temp dir: ${smokeRoot}`);
  info(`Daphne port: ${backendPort}`);
  info(`frontend port: ${frontendPort}`);

  info('migrating temporary database');
  runChecked(uvCommand, ['run', 'python', 'manage.py', 'migrate', '--noinput'], {
    cwd: backendDir,
    env: djangoEnv,
  });
  info('seeding streaming projects');
  const projectIds = seedData(djangoEnv);

  info('starting Daphne ASGI backend');
  backendProcess = startProcess(
    uvCommand,
    ['run', 'daphne', '-b', host, '-p', String(backendPort), 'config.asgi:application'],
    { cwd: backendDir, env: djangoEnv, logPath: backendLogPath },
  );
  await waitForHttp(`http://${host}:${backendPort}/api/v1/users/login/`, [405], 'Daphne backend');

  info('starting webpack dev server');
  frontendProcess = startProcess(
    npmCommand,
    isWindows
      ? ['/d', '/s', '/c', 'npm.cmd', 'run', 'dev', '--', '--host', host, '--port', String(frontendPort), '--no-open']
      : ['run', 'dev', '--', '--host', host, '--port', String(frontendPort), '--no-open'],
    {
      cwd: frontendDir,
      env: {
        BACKEND_PORT: String(backendPort),
        VUE_APP_API_BASE_URL: '/api/v1',
      },
      logPath: frontendLogPath,
    },
  );
  await waitForHttp(appBaseUrl, [200], 'webpack dev server');

  info('running Redis -> Daphne -> EventSource -> Vue UI smoke');
  const browserResult = await runBrowserSmoke(appBaseUrl, projectIds, djangoEnv);
  console.log(JSON.stringify({
    ok: true,
    skipped: false,
    appBaseUrl,
    ...browserResult,
  }, null, 2));
}

main()
  .catch((error) => {
    console.error(`[smoke:streaming] failed: ${error.stack || error.message}`);
    console.error(`[smoke:streaming] Daphne log tail:\n${tail(backendLogPath)}`);
    console.error(`[smoke:streaming] frontend log tail:\n${tail(frontendLogPath)}`);
    process.exitCode = 1;
  })
  .finally(async () => {
    await cleanup();
  });
