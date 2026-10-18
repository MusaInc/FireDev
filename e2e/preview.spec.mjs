// Live preview in the TV interface, driven by remote keys and voice, against a real service: SSH to localhost, a static
// site started by FireDev in tmux, and the preview gateway on its own origin. Runs with preview.config.mjs.
import { expect, test } from '@playwright/test';
import fs from 'node:fs';

const DIR = new URL('./.tmp-preview/', import.meta.url);
const tokens = () => JSON.parse(fs.readFileSync(new URL('tokens.json', DIR)));
const PID = 'web';
const GATEWAY = 'http://127.0.0.1:' + (process.env.FIREDEV_E2E_PREVIEW_PORT || '8136');

const app = (page, fn, arg) => page.evaluate(fn, arg);
const focused = (page) => app(page, () => window.__firedev.focus.focusedId());
const route = (page) => app(page, () => window.__firedev.route.name);
const screen = (page) => app(page, () => { const s = window.__firedev.screen; return { ready: s.ready, interacting: s.interacting, page: s.page, prefs: s.prefs, label: s.focusLabel }; });
async function press(page, ...keys) {
  for (const k of keys) { await page.keyboard.press(k); await page.waitForTimeout(250); }
}
async function say(page, text) {
  await app(page, (t) => window.__firedev.say(t), text);
}
const frame = (page) => page.frames().find((f) => f.url().startsWith(GATEWAY));

async function openWorkspace(page) {
  await page.goto('/tv/?token=' + encodeURIComponent(tokens().tv));
  await page.waitForFunction(() => window.__firedev && window.__firedev.everOnline);
  await app(page, (pid) => window.__firedev.openProject(pid), PID);
  await page.waitForFunction((pid) => { const p = window.__firedev.projects[pid]; return p && p.connection === 'connected'; }, PID, { timeout: 30000 });
}
async function previewReady(page) {
  await page.waitForFunction(() => window.__firedev.route.name === 'preview' && window.__firedev.screen.ready, null, { timeout: 45000, polling: 200 });
}

test.describe.configure({ mode: 'serial' });

test('the workspace offers the live preview; OK starts the dev server and shows the real page from the gateway', async ({ page }) => {
  await openWorkspace(page);
  await expect(page.locator('[data-focus-id="preview"]')).toBeVisible({ timeout: 20000 });
  await expect(page.locator('[data-focus-id="preview"]')).toContainText('Live preview');
  await app(page, () => window.__firedev.focus.focus('preview'));
  await press(page, 'Enter');
  await previewReady(page);
  const f = frame(page);
  expect(f, 'the page comes from the gateway, not the TV interface origin').toBeTruthy();
  await expect(f.locator('#h')).toHaveText('Version one');
  const sandbox = await page.locator('iframe.pv-frame').getAttribute('sandbox');
  expect(sandbox).toContain('allow-scripts');
  expect(sandbox).not.toContain('allow-top-navigation');
  expect(new URL(f.url()).origin).not.toBe(new URL(page.url()).origin);
  expect(await focused(page)).toBe('frame');
  await expect(page.locator('.pv-chips')).toContainText('Live');
  expect((await screen(page)).page).toBe('/');
});

test('OK uses the page: arrows move a focus ring between its links and buttons, OK clicks, Back returns to FireDev', async ({ page }) => {
  await openWorkspace(page);
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await previewReady(page);
  await press(page, 'Enter');
  expect((await screen(page)).interacting).toBe(true);
  await expect(page.locator('.pv-banner')).toContainText('Using the page');
  // First press lands on something on screen; move until the button is chosen.
  for (let i = 0; i < 6 && (await screen(page)).label !== 'Press me'; i++) await press(page, 'ArrowDown');
  expect((await screen(page)).label).toBe('Press me');
  await press(page, 'Enter');
  await expect(frame(page).locator('#b')).toHaveText('Clicked');
  await press(page, 'Escape');
  expect((await screen(page)).interacting).toBe(false);
  expect(await route(page)).toBe('preview');
  await press(page, 'Escape');
  expect(await route(page)).toBe('workspace');
});

test('the page never keeps the keyboard: focus inside the frame is taken back', async ({ page }) => {
  await openWorkspace(page);
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await previewReady(page);
  await frame(page).evaluate(() => document.getElementById('b').focus());
  await page.waitForTimeout(300);
  expect(await app(page, () => document.activeElement && document.activeElement.tagName)).not.toBe('IFRAME');
  await press(page, 'ArrowDown');
  expect(await focused(page)).toMatch(/^tb-/);
});

test('voice: mobile view, fullscreen, zoom, a page by name, then back to Codex', async ({ page }) => {
  await openWorkspace(page);
  await say(page, 'show me the preview');
  await previewReady(page);
  await say(page, 'Show me the mobile version');
  await expect.poll(async () => (await screen(page)).prefs.viewport).toBe('mobile');
  await expect.poll(() => app(page, () => document.querySelector('.pv-device').style.width)).toBe('390px');
  await say(page, 'Show it fullscreen');
  await expect(page.locator('.preview-screen')).toHaveClass(/pv-full/);
  await say(page, 'zoom in');
  await expect.poll(async () => (await screen(page)).prefs.zoom).toBe(1.1);
  await say(page, 'reset zoom');
  await say(page, 'switch to desktop view');
  await expect.poll(async () => (await screen(page)).prefs.viewport).toBe('desktop');
  await say(page, 'go to the about page');
  await expect.poll(async () => (await screen(page)).page, { timeout: 15000 }).toBe('/about.html');
  await say(page, 'go back');
  await expect.poll(async () => (await screen(page)).page, { timeout: 15000 }).toBe('/');
  // Back leaves full screen first, then the preview.
  await press(page, 'Escape');
  await expect(page.locator('.preview-screen')).not.toHaveClass(/pv-full/);
  await say(page, 'Go back to Codex');
  await expect.poll(() => route(page)).toBe('workspace');
});

test('a saved file reloads the page (no hot reload on a static server)', async ({ page }) => {
  await openWorkspace(page);
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await previewReady(page);
  await expect(frame(page).locator('#h')).toHaveText('Version one');
  const index = new URL('web/index.html', DIR);
  fs.writeFileSync(index, fs.readFileSync(index, 'utf8').replace('Version one', 'Version two'));
  await expect.poll(async () => { const f = frame(page); return f ? f.locator('#h').textContent().catch(() => '') : ''; }, { timeout: 20000 }).toBe('Version two');
});

test('a lost preview session recovers by itself with a fresh one-time address', async ({ page, context }) => {
  await openWorkspace(page);
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await previewReady(page);
  await context.clearCookies();
  await app(page, () => window.__firedev.screen.post({ op: 'reload' }));
  await page.waitForTimeout(800);
  await previewReady(page);
  await expect.poll(async () => { const f = frame(page); return f ? f.locator('#h').count().catch(() => 0) : 0; }, { timeout: 20000 }).toBe(1);
});

test('the gateway refuses anyone without a ticket from a paired TV', async ({ request }) => {
  const r = await request.get(GATEWAY + '/');
  expect(r.status()).toBe(403);
  const bad = await request.post('/api/command', { headers: { Authorization: 'Bearer ' + tokens().voice }, data: { name: 'preview_open', args: { project: PID } } });
  expect((await bad.json()).ok).toBe(false);
});
