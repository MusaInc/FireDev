import { defineConfig } from '@playwright/test';

// The preview camera on its own: the page helper in a frame, and the TV's edit-to-element mapping. No service.
export default defineConfig({
  testDir: '.',
  testMatch: 'camera.spec.mjs',
  timeout: 60000,
  workers: 1,
  reporter: [['list']],
  use: { viewport: { width: 1280, height: 720 } },
});
