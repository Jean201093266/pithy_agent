# Signing and Update Channel

## Signing modes

- **Unsigned build (default local)**
  - Use: `npm run dist:win`
  - Installer builds without certificate signing metadata.

- **Signed build (release mode)**
  - Use: `npm run dist:win:signed`
  - Required environment variables:
    - `CSC_LINK` and `CSC_KEY_PASSWORD`
    - or `WIN_CSC_LINK` and `WIN_CSC_KEY_PASSWORD`

The script `npm run release:signing:check` validates these variables before building.

## Update metadata

`npm run release:prepare` writes:

- `app/static/build-info.json`
- `dist/release-manifest.json`
- release notes block in `RELEASE_NOTES.md` (if missing)

`npm run release:artifacts` writes:

- `dist/checksums.txt`
- `dist/publish-index.json`
- `dist/upload-assets.json`
- updates `dist/release-manifest.json` with installer signature inspection fields

`electron-builder` writes:

- `dist/latest.yml`
- `dist/<installer>.blockmap`

## Verification

Run:

```powershell
npm run release:verify
```

For signed release gating:

```powershell
npm run release:verify:signed
```

Validation includes:

- installer exists and matches `latest.yml.path`
- `sha512` in `latest.yml` matches installer payload
- `files[]` entry is consistent with top-level metadata
- blockmap and release manifest exist
- checksums and publish/upload manifests are present and consistent
- signing metadata is present and consistent across release/publish/upload manifests
- in `--require-signed` mode, installer must be signed and subject must exist

## One-command release pipeline

```powershell
npm run release:pipeline
```

This runs:

1. `dist:win`
2. `release:artifacts`
3. `release:verify`

Use this as the minimum local release gate before publishing artifacts.

## GitHub upload channel

Upload verified artifacts to GitHub Releases:

```powershell
set GITHUB_TOKEN=ghp_xxx
set GITHUB_OWNER=your-owner
set GITHUB_REPO=your-repo
npm run release:upload:github
```

Optional environment variables:

- `RELEASE_TAG` (default: `v<version>`)
- `RELEASE_NAME` (default: `PithyLocalAgent v<version>`)
- `RELEASE_DRAFT` (`true`/`false`)
- `RELEASE_PRERELEASE` (`true`/`false`)

