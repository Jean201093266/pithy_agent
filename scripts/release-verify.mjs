import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import YAML from 'yaml';

const root = process.cwd();
const distDir = path.join(root, 'dist');
const requireSigned = process.argv.includes('--require-signed');

function fail(msg) {
  console.error(`[release-verify] ${msg}`);
  process.exit(1);
}

function sha512File(filePath) {
  const hash = crypto.createHash('sha512');
  const data = fs.readFileSync(filePath);
  hash.update(data);
  return hash.digest('base64');
}

if (!fs.existsSync(distDir)) fail('dist directory not found. Run npm run dist:win first.');

const latestPath = path.join(distDir, 'latest.yml');
const manifestPath = path.join(distDir, 'release-manifest.json');
const checksumsPath = path.join(distDir, 'checksums.txt');
const publishIndexPath = path.join(distDir, 'publish-index.json');
const uploadAssetsPath = path.join(distDir, 'upload-assets.json');
const installerCandidates = fs
  .readdirSync(distDir)
  .filter((name) => name.endsWith('.exe') && !name.startsWith('__uninstaller'));

if (!fs.existsSync(latestPath)) fail('latest.yml missing in dist/.');
if (!fs.existsSync(manifestPath)) fail('release-manifest.json missing in dist/.');
if (!fs.existsSync(checksumsPath)) fail('checksums.txt missing in dist/. Run npm run release:artifacts first.');
if (!fs.existsSync(publishIndexPath)) fail('publish-index.json missing in dist/. Run npm run release:artifacts first.');
if (!fs.existsSync(uploadAssetsPath)) fail('upload-assets.json missing in dist/. Run npm run release:artifacts first.');
if (!installerCandidates.length) fail('No installer exe found in dist/.');

const latestRaw = fs.readFileSync(latestPath, 'utf-8');
const latest = YAML.parse(latestRaw);
const releaseManifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));

if (!latest || typeof latest !== 'object') fail('latest.yml parse failed.');
if (!latest.version) fail('latest.yml missing version.');
if (!latest.path) fail('latest.yml missing path.');
if (!latest.sha512) fail('latest.yml missing sha512.');
if (!latest.releaseDate) fail('latest.yml missing releaseDate.');
if (!Array.isArray(latest.files) || !latest.files.length) fail('latest.yml missing files array.');

const installerName = latest.path;
const installerPath = path.join(distDir, installerName);
if (!fs.existsSync(installerPath)) fail(`Installer referenced by latest.yml not found: ${installerName}`);

const computedSha = sha512File(installerPath);
if (computedSha !== latest.sha512) fail('sha512 mismatch between latest.yml and installer payload.');

const filesEntry = latest.files.find((f) => f.url === installerName);
if (!filesEntry) fail('latest.yml files[] does not contain installer url entry.');
if (filesEntry.sha512 !== latest.sha512) fail('latest.yml files[] sha512 does not match top-level sha512.');

const blockmapPath = path.join(distDir, `${installerName}.blockmap`);
if (!fs.existsSync(blockmapPath)) fail('Installer blockmap is missing.');

const checksums = fs.readFileSync(checksumsPath, 'utf-8');
if (!checksums.includes(`  ${installerName}`)) fail('checksums.txt does not include installer entry.');
if (!checksums.includes(`  ${installerName}.blockmap`)) fail('checksums.txt does not include blockmap entry.');

const publishIndex = JSON.parse(fs.readFileSync(publishIndexPath, 'utf-8'));
if (publishIndex.version !== latest.version) fail('publish-index version does not match latest.yml version.');
if (publishIndex.installer?.name !== installerName) fail('publish-index installer name mismatch.');
if (publishIndex.installer?.sha512_base64 !== latest.sha512) fail('publish-index installer sha512 mismatch.');

if (!releaseManifest.signing || typeof releaseManifest.signing !== 'object') {
  fail('release-manifest.json missing signing metadata.');
}
if (typeof releaseManifest.signing.isSigned !== 'boolean') {
  fail('release-manifest signing.isSigned must be boolean.');
}
if (!publishIndex.signing || publishIndex.signing.isSigned !== releaseManifest.signing.isSigned) {
  fail('publish-index signing metadata mismatch.');
}

const uploadAssets = JSON.parse(fs.readFileSync(uploadAssetsPath, 'utf-8'));
if (!Array.isArray(uploadAssets.files) || uploadAssets.files.length < 4) {
  fail('upload-assets.json files list is incomplete.');
}
const uploadInstaller = uploadAssets.files.find((f) => f.name === installerName);
if (!uploadInstaller) fail('upload-assets.json missing installer file entry.');
if (!uploadAssets.signing || uploadAssets.signing.isSigned !== releaseManifest.signing.isSigned) {
  fail('upload-assets signing metadata mismatch.');
}

if (requireSigned) {
  if (!releaseManifest.signing.isSigned) {
    fail('signed release required but installer is not signed.');
  }
  if (!releaseManifest.signing.subject) {
    fail('signed release required but signing subject is missing.');
  }
}

const summary = {
  installer: installerName,
  version: latest.version,
  releaseDate: latest.releaseDate,
  sha512: latest.sha512,
  manifest: 'release-manifest.json',
  blockmap: `${installerName}.blockmap`,
  checksums: 'checksums.txt',
  publishIndex: 'publish-index.json',
  uploadAssets: 'upload-assets.json',
  signing: releaseManifest.signing,
  requireSigned,
};

console.log('[release-verify] OK');
console.log(JSON.stringify(summary, null, 2));

