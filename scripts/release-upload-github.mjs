import fs from 'fs';
import path from 'path';

const token = process.env.GITHUB_TOKEN;
const owner = process.env.GITHUB_OWNER;
const repo = process.env.GITHUB_REPO;

function fail(msg) {
  console.error(`[release-upload-github] ${msg}`);
  process.exit(1);
}

if (!token) fail('GITHUB_TOKEN is required.');
if (!owner || !repo) fail('GITHUB_OWNER and GITHUB_REPO are required.');

const root = process.cwd();
const distDir = path.join(root, 'dist');
const uploadAssetsPath = path.join(distDir, 'upload-assets.json');
const releaseManifestPath = path.join(distDir, 'release-manifest.json');

if (!fs.existsSync(uploadAssetsPath)) fail('upload-assets.json missing. Run npm run release:artifacts first.');
if (!fs.existsSync(releaseManifestPath)) fail('release-manifest.json missing. Run npm run release:prepare first.');

const uploadAssets = JSON.parse(fs.readFileSync(uploadAssetsPath, 'utf-8'));
const releaseManifest = JSON.parse(fs.readFileSync(releaseManifestPath, 'utf-8'));
const version = uploadAssets.version || releaseManifest.version;
if (!version) fail('version missing from release metadata.');

const tag = process.env.RELEASE_TAG || `v${version}`;
const releaseName = process.env.RELEASE_NAME || `PithyLocalAgent ${tag}`;
const draft = String(process.env.RELEASE_DRAFT || 'false').toLowerCase() === 'true';
const prerelease = String(process.env.RELEASE_PRERELEASE || 'false').toLowerCase() === 'true';

const baseApi = `https://api.github.com/repos/${owner}/${repo}`;
const headers = {
  Authorization: `Bearer ${token}`,
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
};

async function ghJson(url, options = {}) {
  const resp = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers || {}), 'Content-Type': 'application/json' },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json();
}

async function getOrCreateRelease() {
  try {
    return await ghJson(`${baseApi}/releases/tags/${encodeURIComponent(tag)}`);
  } catch {
    return ghJson(`${baseApi}/releases`, {
      method: 'POST',
      body: JSON.stringify({
        tag_name: tag,
        name: releaseName,
        draft,
        prerelease,
      }),
    });
  }
}

async function uploadAsset(uploadUrlTemplate, filePath, fileName) {
  const uploadBase = uploadUrlTemplate.replace('{?name,label}', '');
  const uploadUrl = `${uploadBase}?name=${encodeURIComponent(fileName)}`;
  const bytes = fs.readFileSync(filePath);

  const resp = await fetch(uploadUrl, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/octet-stream',
    },
    body: bytes,
  });

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`upload failed for ${fileName}: ${resp.status} ${resp.statusText}: ${body}`);
  }

  return resp.json();
}

async function ensureUpload() {
  const release = await getOrCreateRelease();
  const current = await ghJson(`${baseApi}/releases/${release.id}/assets`);

  const required = Array.from(new Set(uploadAssets.files.map((f) => f.name).concat(['latest.yml', 'release-manifest.json'])));

  for (const fileName of required) {
    const fullPath = path.join(distDir, fileName);
    if (!fs.existsSync(fullPath)) fail(`missing dist file for upload: ${fileName}`);

    const existing = current.find((a) => a.name === fileName);
    if (existing) {
      await ghJson(`${baseApi}/releases/assets/${existing.id}`, { method: 'DELETE' });
      console.log(`[release-upload-github] replaced asset: ${fileName}`);
    }

    await uploadAsset(release.upload_url, fullPath, fileName);
    console.log(`[release-upload-github] uploaded: ${fileName}`);
  }

  console.log(`[release-upload-github] completed release upload for ${tag}`);
}

ensureUpload().catch((err) => fail(err.message));

