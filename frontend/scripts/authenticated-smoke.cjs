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
const smokeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-story-auth-smoke-'));
const sqlitePath = path.join(smokeRoot, 'smoke.sqlite3');
const backendLogPath = path.join(smokeRoot, 'backend.log');
const frontendLogPath = path.join(smokeRoot, 'frontend.log');

const npmCommand = isWindows ? 'cmd.exe' : 'npm';
const uvCommand = 'uv';
const host = process.env.SMOKE_HOST || '127.0.0.1';
const username = process.env.SMOKE_USERNAME || 'codex_smoke';
const password = process.env.SMOKE_PASSWORD || 'CodexSmoke123!';
const keepArtifacts = process.env.SMOKE_KEEP_ARTIFACTS === '1';
const timeoutMs = Number(process.env.SMOKE_TIMEOUT_MS || 120000);

const ASSET_BUTTON_TEXT = '\u8d44\u4ea7 0';
const ASSET_DRAWER_TEXT = '\u8d44\u4ea7\u53d8\u91cf';

let backendProcess = null;
let frontendProcess = null;
let browser = null;
let cleaned = false;

function info(message) {
  console.log(`[smoke:auth] ${message}`);
}

function tail(filePath, maxChars = 4000) {
  if (!fs.existsSync(filePath)) {
    return '';
  }
  const content = fs.readFileSync(filePath, 'utf8');
  return content.slice(-maxChars);
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
    server.on('error', () => {
      resolve(findFreePort(startPort + 1));
    });
    server.listen({ host, port: startPort }, () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function waitForHttp(url, expectedStatuses, label) {
  const startedAt = Date.now();
  let lastError = '';

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await new Promise((resolve, reject) => {
        const request = http.get(url, { timeout: 5000 }, resolve);
        request.on('timeout', () => {
          request.destroy(new Error('request timeout'));
        });
        request.on('error', reject);
      });

      response.resume();
      if (expectedStatuses.includes(response.statusCode)) {
        return response.statusCode;
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
  let logStreamEnded = false;
  const writeLog = (message) => {
    if (!logStreamEnded) {
      logStream.write(message);
    }
  };
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.on('close', (code, signal) => {
    writeLog(`\n[process exit] code=${code} signal=${signal}\n`);
    logStreamEnded = true;
    logStream.end();
  });
  child.on('error', (error) => {
    writeLog(`\n[process error] ${error.stack || error.message}\n`);
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
      // Ignore close errors during cleanup.
    }
  }

  stopProcess(frontendProcess);
  stopProcess(backendProcess);

  if (keepArtifacts) {
    info(`kept artifacts at ${smokeRoot}`);
    return;
  }

  fs.rmSync(smokeRoot, { recursive: true, force: true });
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
    DJANGO_SETTINGS_MODULE: 'config.settings.base',
    SQLITE_DB_PATH: sqlitePath,
    PYTHONIOENCODING: 'utf-8',
  };
}

function seedData() {
  const seedCode = `
from django.contrib.auth import get_user_model
from apps.content.models import CameraMovement, Storyboard
from apps.projects.models import ProjectStage, Series
from apps.projects.serializers import create_project_with_resources
from apps.prompts.models import PromptTemplateSet

username = ${JSON.stringify(username)}
password = ${JSON.stringify(password)}
User = get_user_model()
user, _ = User.objects.update_or_create(
    username=username,
    defaults={'email': 'codex-smoke@example.local', 'is_active': True},
)
user.set_password(password)
user.save()

Series.objects.filter(user=user, name='Codex Smoke Series').delete()
series = Series.objects.create(
    user=user,
    name='Codex Smoke Series',
    description='Temporary smoke series for authenticated browser validation.',
)

PromptTemplateSet.objects.filter(created_by=user, name='Codex Smoke Prompt Set').delete()
PromptTemplateSet.objects.create(
    created_by=user,
    name='Codex Smoke Prompt Set',
    description='Temporary prompt set for authenticated browser validation.',
    is_active=True,
    is_default=False,
)

project = create_project_with_resources({
    'series': series,
    'name': 'Episode 1',
    'episode_number': 1,
    'episode_title': 'Codex Smoke Episode',
    'sort_order': 1,
    'description': 'Temporary smoke project for authenticated browser validation.',
    'original_topic': 'A concise story about validating an AI video workflow canvas.',
}, user)

ProjectStage.objects.filter(project=project, stage_type='rewrite').update(
    status='completed',
    output_data={'rewritten_text': 'A validated opening scene for the smoke run.'},
)
ProjectStage.objects.filter(project=project, stage_type='storyboard').update(status='completed')
storyboard = Storyboard.objects.create(
    project=project,
    sequence_number=1,
    scene_description='Opening shot of the workflow canvas validation scene.',
    narration_text='The authenticated smoke run reaches the project detail canvas.',
    image_prompt='clean product interface validation scene, crisp lighting',
    duration_seconds=3.0,
)
CameraMovement.objects.create(
    storyboard=storyboard,
    movement_type='static',
    movement_params={'description': 'Static establishing shot'},
)

print('SMOKE_PROJECT_ID=' + str(project.id))
print('SMOKE_SERIES_ID=' + str(series.id))
`;

  const result = runChecked(uvCommand, ['run', 'python', 'manage.py', 'shell', '-c', seedCode], {
    cwd: backendDir,
    env: buildDjangoEnv(),
    timeout: timeoutMs,
  });

  const projectMatch = result.stdout.match(/SMOKE_PROJECT_ID=([0-9a-f-]+)/i);
  if (!projectMatch) {
    throw new Error(`Seed command did not return a project id.\n${commandOutput(result)}`);
  }

  return projectMatch[1];
}

async function launchBrowser() {
  const attempts = [];
  if (process.env.PLAYWRIGHT_CHANNEL) {
    attempts.push({ channel: process.env.PLAYWRIGHT_CHANNEL });
  } else {
    attempts.push({});
    attempts.push({ channel: 'msedge' });
    attempts.push({ channel: 'chrome' });
  }

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
    `Unable to launch a Playwright browser.\n${errors.join('\n')}\n` +
    'Run `npx playwright install chromium` or set PLAYWRIGHT_CHANNEL=msedge/chrome.'
  );
}

async function assertPage(page, url, expectedText) {
  await page.goto(url);
  await page.waitForLoadState('domcontentloaded');
  await page.getByText(expectedText).first().waitFor({ timeout: 30000 });

  const bodyText = await page.locator('body').innerText({ timeout: 10000 });
  if (bodyText.trim().length < 40) {
    throw new Error(`Page appears blank: ${url}`);
  }

  if (/compiled with problems|runtime error|module build failed|webpack-dev-server error/i.test(bodyText)) {
    throw new Error(`Framework overlay detected on ${url}`);
  }

  return {
    url: page.url(),
    title: await page.title(),
    expectedText,
  };
}

async function runBrowserSmoke(appBaseUrl, projectId) {
  browser = await launchBrowser();
  const context = await browser.newContext({
    baseURL: appBaseUrl,
    viewport: { width: 1280, height: 720 },
  });
  const page = await context.newPage();
  const logs = [];
  const networkErrors = [];

  page.on('console', (message) => {
    if (
      ['error', 'warning'].includes(message.type()) &&
      !/^Failed to load resource:/i.test(message.text())
    ) {
      logs.push({
        type: message.type(),
        text: message.text(),
      });
    }
  });
  page.on('pageerror', (error) => {
    logs.push({
      type: 'pageerror',
      text: error.message,
    });
  });
  page.on('response', (response) => {
    const status = response.status();
    if (status >= 400) {
      networkErrors.push({
        status,
        url: response.url(),
      });
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

  const pages = [];
  pages.push(await assertPage(page, `${appBaseUrl}/series`, 'Codex Smoke Series'));
  pages.push(await assertPage(page, `${appBaseUrl}/projects/${projectId}`, 'Codex Smoke Episode'));

  const assetButton = page.locator('button').filter({ hasText: ASSET_BUTTON_TEXT });
  const assetButtonCount = await assetButton.count();
  if (assetButtonCount !== 1) {
    throw new Error(`Expected one asset drawer button, found ${assetButtonCount}`);
  }
  await assetButton.click();
  await page.getByText(ASSET_DRAWER_TEXT).first().waitFor({ timeout: 10000 });

  pages.push(await assertPage(page, `${appBaseUrl}/prompts`, 'Codex Smoke Prompt Set'));
  pages.push(await assertPage(page, `${appBaseUrl}/models`, 'Mock LLM API'));

  await page.waitForTimeout(1000);
  const relevantLogs = logs.filter((entry) => {
    return !/favicon\.ico/i.test(entry.text);
  });
  const relevantNetworkErrors = networkErrors.filter((entry) => {
    if (entry.status === 404 && /\/api\/v1\/agent\//.test(entry.url)) {
      return false;
    }
    if (entry.status === 404 && /\/favicon\.ico$/.test(entry.url)) {
      return false;
    }
    return true;
  });
  if (relevantLogs.length) {
    throw new Error(`Browser console reported errors/warnings:\n${JSON.stringify(relevantLogs, null, 2)}`);
  }
  if (relevantNetworkErrors.length) {
    throw new Error(`Browser network reported failing responses:\n${JSON.stringify(relevantNetworkErrors, null, 2)}`);
  }

  await context.close();
  await browser.close();
  browser = null;

  return pages;
}

async function main() {
  installSignalHandlers();

  const backendPort = Number(process.env.BACKEND_PORT || await findFreePort(8010));
  const frontendPort = Number(process.env.FRONTEND_PORT || await findFreePort(13020));
  const appBaseUrl = process.env.SMOKE_APP_URL || `http://${host}:${frontendPort}`;
  const djangoEnv = buildDjangoEnv();

  info(`temp dir: ${smokeRoot}`);
  info(`backend port: ${backendPort}`);
  info(`frontend port: ${frontendPort}`);

  info('migrating temporary database');
  runChecked(uvCommand, ['run', 'python', 'manage.py', 'migrate', '--noinput'], {
    cwd: backendDir,
    env: djangoEnv,
    timeout: timeoutMs,
  });

  info('seeding authenticated smoke data');
  const projectId = seedData();

  info('starting backend');
  backendProcess = startProcess(
    uvCommand,
    ['run', 'python', 'manage.py', 'runserver', `${host}:${backendPort}`, '--noreload'],
    {
      cwd: backendDir,
      env: djangoEnv,
      logPath: backendLogPath,
    }
  );
  await waitForHttp(`http://${host}:${backendPort}/api/v1/users/login/`, [405], 'Django backend');

  info('starting frontend dev server');
  frontendProcess = startProcess(
    npmCommand,
    isWindows
      ? ['/d', '/s', '/c', 'npm.cmd', 'run', 'dev', '--', '--host', host, '--port', String(frontendPort), '--no-open']
      : ['run', 'dev', '--', '--host', host, '--port', String(frontendPort), '--no-open'],
    {
      cwd: frontendDir,
      env: {
        BACKEND_PORT: String(backendPort),
      },
      logPath: frontendLogPath,
    }
  );
  await waitForHttp(appBaseUrl, [200], 'webpack dev server');

  info('running Playwright authenticated smoke');
  const pages = await runBrowserSmoke(appBaseUrl, projectId);

  console.log(JSON.stringify({
    ok: true,
    appBaseUrl,
    projectId,
    pages,
  }, null, 2));
}

main()
  .catch((error) => {
    console.error(`[smoke:auth] failed: ${error.stack || error.message}`);
    console.error(`[smoke:auth] backend log tail:\n${tail(backendLogPath)}`);
    console.error(`[smoke:auth] frontend log tail:\n${tail(frontendLogPath)}`);
    process.exitCode = 1;
  })
  .finally(async () => {
    await cleanup();
  });
