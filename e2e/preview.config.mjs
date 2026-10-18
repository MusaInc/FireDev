// Live preview browser tests: their own throwaway service (a static web project and the preview gateway).
import { defineConfig } from '@playwright/test';

const port = process.env.FIREDEV_E2E_PORT || '8137';
export default defineConfig({
  testDir: '.',
  testMatch: 'preview.spec.mjs',
  timeout: 90000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1920, height: 1080 }, screenshot: 'only-on-failure' },
  webServer: {
    command: `../.venv/bin/python server.py`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 30000,
    env: { FIREDEV_E2E_PORT: port, FIREDEV_E2E_SCENARIO: 'preview', FIREDEV_E2E_DIR: new URL('./.tmp-preview', import.meta.url).pathname },
  },
});
