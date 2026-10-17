import { defineConfig } from '@playwright/test';

const port = process.env.FIREDEV_E2E_PORT || '8139';
export default defineConfig({
  testDir: '.',
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
    env: { FIREDEV_E2E_PORT: port },
  },
});
