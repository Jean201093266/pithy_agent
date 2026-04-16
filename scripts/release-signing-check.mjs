import process from 'process';

const hasWindowsSign = Boolean(process.env.CSC_LINK && process.env.CSC_KEY_PASSWORD);
const hasLegacyWindowsSign = Boolean(process.env.WIN_CSC_LINK && process.env.WIN_CSC_KEY_PASSWORD);

if (!hasWindowsSign && !hasLegacyWindowsSign) {
  console.error('[release-signing-check] Missing signing env vars.');
  console.error('Provide either CSC_LINK + CSC_KEY_PASSWORD or WIN_CSC_LINK + WIN_CSC_KEY_PASSWORD.');
  process.exit(1);
}

console.log('[release-signing-check] Signing configuration detected.');

