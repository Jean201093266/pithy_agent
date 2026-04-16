# Release Checklist

## Preflight

- [ ] `python -m pytest -q` passes
- [ ] `npm run test:e2e` passes
- [ ] `python run.py` starts locally
- [ ] OCR status in UI is verified

## Build

- [ ] `npm run release:prepare` completed
- [ ] `npm run dist:win` completed
- [ ] (optional signed release) `npm run dist:win:signed` completed
- [ ] `npm run release:artifacts` completed
- [ ] `npm run release:verify` completed
- [ ] (signed gate) `npm run release:verify:signed` completed
- [ ] Installer exists: `dist/PithyLocalAgent-Setup-<version>.exe`
- [ ] Portable build exists: `dist/win-unpacked/`
- [ ] `dist/release-manifest.json` generated
- [ ] `dist/latest.yml` matches installer hash
- [ ] `dist/checksums.txt` / `dist/publish-index.json` / `dist/upload-assets.json` generated
- [ ] signing fields (`isSigned`, `subject`) present in publish/release manifests

## Smoke Test

- [ ] Install from NSIS package on clean machine/user
- [ ] Launch app and verify backend auto-start
- [ ] Verify lock/unlock flow
- [ ] Verify chat + custom tools + visual skill editor

## Publish

- [ ] Update `RELEASE_NOTES.md` highlights
- [ ] Upload installer and checksums
- [ ] (GitHub) `npm run release:upload:github` completed
- [ ] Archive `dist/builder-effective-config.yaml`

