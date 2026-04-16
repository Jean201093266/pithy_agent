import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const root = process.cwd();
const pkgPath = path.join(root, 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));

const version = pkg.version;
const now = new Date();
const buildTime = now.toISOString();
let commit = 'unknown';
let commits = [];

try {
  commit = execSync('git rev-parse --short HEAD', { cwd: root, stdio: ['ignore', 'pipe', 'ignore'] })
    .toString()
    .trim();
} catch {
  commit = 'unknown';
}

try {
  const out = execSync('git --no-pager log --pretty=format:%s -n 15', {
    cwd: root,
    stdio: ['ignore', 'pipe', 'ignore'],
  })
    .toString()
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  commits = out;
} catch {
  commits = [];
}

const buildInfo = {
  name: pkg.name,
  productName: pkg.build?.productName || pkg.name,
  version,
  buildTime,
  commit,
  commits,
  signing: {
    status: 'unknown',
    isSigned: false,
    subject: null,
    issuer: null,
    thumbprint: null,
    notAfter: null,
  },
};

const staticDir = path.join(root, 'app', 'static');
fs.mkdirSync(staticDir, { recursive: true });
fs.writeFileSync(path.join(staticDir, 'build-info.json'), JSON.stringify(buildInfo, null, 2));

const distDir = path.join(root, 'dist');
fs.mkdirSync(distDir, { recursive: true });
fs.writeFileSync(path.join(distDir, 'release-manifest.json'), JSON.stringify(buildInfo, null, 2));

const notesPath = path.join(root, 'RELEASE_NOTES.md');
const releaseHeader = `## v${version} - ${buildTime.slice(0, 10)}`;
const existing = fs.existsSync(notesPath) ? fs.readFileSync(notesPath, 'utf-8') : '# Release Notes\n\n';
if (!existing.includes(releaseHeader)) {
  const commitLines = commits.length ? commits.map((c) => `- ${c}`).join('\n') : '- Initial release metadata generated';
  const block = `${releaseHeader}\n\n### Highlights\n\n- Update this section with release summary.\n\n### Commits\n\n${commitLines}\n\n`;
  fs.writeFileSync(notesPath, `${existing.trimEnd()}\n\n${block}`);
}

console.log(`[release-prepare] version=${version} commit=${commit}`);

