#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const rootDir = path.resolve(__dirname, '..');
const srcDir = path.join(rootDir, 'src');
const asJson = process.argv.includes('--json');

const patterns = [
  {
    id: 'vue-global-api',
    label: 'Vue 2 global API bootstrap',
    severity: 'migration',
    regex: /\b(?:new\s+Vue\s*\(|Vue\.use\s*\(|Vue\.prototype\b)/,
  },
  {
    id: 'vue2-lifecycle',
    label: 'Vue 2 lifecycle names',
    severity: 'migration',
    regex: /\b(?:beforeDestroy|destroyed)\s*\(/,
  },
  {
    id: 'vue2-reactivity-helper',
    label: 'Vue 2 reactivity helpers',
    severity: 'migration',
    regex: /\bthis\.\$(?:set|delete)\s*\(/,
  },
  {
    id: 'vuex-options-api',
    label: 'Vuex store/helper usage',
    severity: 'inventory',
    regex: /\b(?:import\s+.*\s+from\s+['"]vuex['"]|new\s+Vuex\.Store\s*\(|map(?:State|Getters|Actions|Mutations)\s*\()/,
  },
  {
    id: 'vue2-template-api',
    label: 'Vue 2 template compatibility APIs',
    severity: 'check',
    regex: /(?:slot-scope|\$listeners|\.sync\b|\.native\b)/,
  },
];

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'dist') {
        continue;
      }
      files.push(...walk(fullPath));
      continue;
    }

    if (entry.isFile() && /\.(js|vue)$/.test(entry.name)) {
      files.push(fullPath);
    }
  }

  return files;
}

function scanFile(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  return content.split(/\r?\n/).flatMap((line, index) => {
    return patterns
      .filter((pattern) => pattern.regex.test(line))
      .map((pattern) => ({
        id: pattern.id,
        label: pattern.label,
        severity: pattern.severity,
        file: path.relative(rootDir, filePath).replace(/\\/g, '/'),
        line: index + 1,
        text: line.trim().slice(0, 180),
      }));
  });
}

const files = walk(srcDir);
const findings = files.flatMap(scanFile);
const summary = patterns.map((pattern) => ({
  id: pattern.id,
  label: pattern.label,
  severity: pattern.severity,
  count: findings.filter((finding) => finding.id === pattern.id).length,
}));

if (asJson) {
  process.stdout.write(JSON.stringify({
    root: rootDir,
    filesScanned: files.length,
    totalFindings: findings.length,
    summary,
    findings,
  }, null, 2));
  process.stdout.write('\n');
} else {
  console.log('Vue 2 compatibility audit');
  console.log(`Root: ${rootDir}`);
  console.log(`Files scanned: ${files.length}`);
  console.log(`Findings: ${findings.length}`);
  console.log('');
  console.log('Summary');
  for (const item of summary) {
    console.log(`- ${item.id}: ${item.count} (${item.severity}) ${item.label}`);
  }
  console.log('');
  console.log('Findings');
  for (const finding of findings) {
    console.log(`${finding.file}:${finding.line} [${finding.id}] ${finding.text}`);
  }
}
