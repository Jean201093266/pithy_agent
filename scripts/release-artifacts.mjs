import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { execSync } from 'child_process';
import YAML from 'yaml';

const root = process.cwd();
const distDir = path.join(root, 'dist');

function fail(msg) {
  console.error(`[release-artifacts] ${msg}`);
  process.exit(1);
}

function hashFile(filePath, algorithm) {
  const hash = crypto.createHash(algorithm);
  hash.update(fs.readFileSync(filePath));
  return hash.digest('hex');
}

function hashFileBase64(filePath, algorithm) {
  const hash = crypto.createHash(algorithm);
  hash.update(fs.readFileSync(filePath));
  return hash.digest('base64');
}

function inspectWindowsSignature(filePath) {
  try {
    const safePath = filePath.replace(/'/g, "''");
    const psCmd = [
      "$sig = Get-AuthenticodeSignature -FilePath '" + safePath + "'",
      "$obj = [ordered]@{",
      "status = [string]$sig.Status",
      "isSigned = ($sig.Status -eq 'Valid')",
      "subject = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { $null }",
      "issuer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Issuer } else { $null }",
      "thumbprint = if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint } else { $null }",
      "notAfter = if ($sig.SignerCertificate) { $sig.SignerCertificate.NotAfter.ToString('o') } else { $null }",
      "}",
      "$obj | ConvertTo-Json -Compress",
    ].join('; ');
    const out = execSync(`powershell -NoProfile -Command "${psCmd}"`, {
      stdio: ['ignore', 'pipe', 'ignore'],
    })
      .toString()
      .trim();
    const info = JSON.parse(out);
    return {
      status: String(info.status || 'unknown'),
      isSigned: Boolean(info.isSigned),
      subject: info.subject || null,
      issuer: info.issuer || null,
      thumbprint: info.thumbprint || null,
      notAfter: info.notAfter || null,
    };
  } catch {
    return {
      status: 'unknown',
      isSigned: false,
      subject: null,
      issuer: null,
      thumbprint: null,
      notAfter: null,
    };
  }
}

if (!fs.existsSync(distDir)) fail('dist directory not found. Run npm run dist:win first.');

const latestPath = path.join(distDir, 'latest.yml');
const manifestPath = path.join(distDir, 'release-manifest.json');
if (!fs.existsSync(latestPath)) fail('latest.yml not found.');
if (!fs.existsSync(manifestPath)) fail('release-manifest.json not found.');

const latest = YAML.parse(fs.readFileSync(latestPath, 'utf-8'));
if (!latest?.path) fail('latest.yml missing path.');

const installerName = latest.path;
const installerPath = path.join(distDir, installerName);
const blockmapName = `${installerName}.blockmap`;
const blockmapPath = path.join(distDir, blockmapName);

if (!fs.existsSync(installerPath)) fail(`installer not found: ${installerName}`);
if (!fs.existsSync(blockmapPath)) fail(`blockmap not found: ${blockmapName}`);

const releaseManifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
const signing = inspectWindowsSignature(installerPath);
releaseManifest.signing = signing;
fs.writeFileSync(manifestPath, JSON.stringify(releaseManifest, null, 2));

const assets = [
  {
    name: installerName,
    path: installerPath,
    size: fs.statSync(installerPath).size,
    sha256: hashFile(installerPath, 'sha256'),
    sha512_base64: hashFileBase64(installerPath, 'sha512'),
    kind: 'installer',
  },
  {
    name: blockmapName,
    path: blockmapPath,
    size: fs.statSync(blockmapPath).size,
    sha256: hashFile(blockmapPath, 'sha256'),
    sha512_base64: hashFileBase64(blockmapPath, 'sha512'),
    kind: 'blockmap',
  },
  {
    name: 'latest.yml',
    path: latestPath,
    size: fs.statSync(latestPath).size,
    sha256: hashFile(latestPath, 'sha256'),
    sha512_base64: hashFileBase64(latestPath, 'sha512'),
    kind: 'metadata',
  },
  {
    name: 'release-manifest.json',
    path: manifestPath,
    size: fs.statSync(manifestPath).size,
    sha256: hashFile(manifestPath, 'sha256'),
    sha512_base64: hashFileBase64(manifestPath, 'sha512'),
    kind: 'metadata',
  },
];

const checksums = assets
  .map((a) => `${a.sha256}  ${a.name}`)
  .join('\n') + '\n';
fs.writeFileSync(path.join(distDir, 'checksums.txt'), checksums);

const publishIndex = {
  productName: releaseManifest.productName || 'PithyLocalAgent',
  version: releaseManifest.version,
  buildTime: releaseManifest.buildTime,
  channel: 'latest',
  signing,
  installer: assets.find((a) => a.kind === 'installer'),
  blockmap: assets.find((a) => a.kind === 'blockmap'),
  metadata: {
    latestYml: 'latest.yml',
    releaseManifest: 'release-manifest.json',
  },
};
fs.writeFileSync(path.join(distDir, 'publish-index.json'), JSON.stringify(publishIndex, null, 2));

const uploadAssets = {
  version: releaseManifest.version,
  signing,
  files: assets.map((a) => ({
    name: a.name,
    size: a.size,
    sha256: a.sha256,
    kind: a.kind,
  })).concat([
    {
      name: 'checksums.txt',
      size: fs.statSync(path.join(distDir, 'checksums.txt')).size,
      sha256: hashFile(path.join(distDir, 'checksums.txt'), 'sha256'),
      kind: 'metadata',
    },
    {
      name: 'publish-index.json',
      size: fs.statSync(path.join(distDir, 'publish-index.json')).size,
      sha256: hashFile(path.join(distDir, 'publish-index.json'), 'sha256'),
      kind: 'metadata',
    },
  ]),
};
fs.writeFileSync(path.join(distDir, 'upload-assets.json'), JSON.stringify(uploadAssets, null, 2));

console.log('[release-artifacts] generated checksums.txt, publish-index.json, upload-assets.json');

