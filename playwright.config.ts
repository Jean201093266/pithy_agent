import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8000',
    headless: true,
  },
  webServer: {
    command: 'python run.py',
    url: 'http://127.0.0.1:8000/api/health',
    timeout: 120_000,
    reuseExistingServer: true,
  },
});

