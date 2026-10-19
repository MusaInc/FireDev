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

test('updates never blank the page: HTML swaps in once loaded, CSS changes apply in place without a reload', async ({ page }) => {
  await openWorkspace(page);
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await previewReady(page);
  const shown = () => page.locator('iframe.pv-frame:not(.pv-next)').contentFrame();
  const index = new URL('web/index.html', DIR);
  const css = new URL('web/site.css', DIR);
  fs.writeFileSync(css, '#h { color: rgb(10, 20, 30); }');
  fs.writeFileSync(index, fs.readFileSync(index, 'utf8').replace('</head>', '<link rel="stylesheet" href="/site.css"></head>').replace(/Version \w+/, 'Version three'));
  // While the new version loads, the old one stays on screen: sample visibility through the whole swap.
  const samples = await app(page, () => new Promise((done) => {
    const out = [];
    const t = setInterval(() => {
      const s = window.__firedev.screen;
      out.push(s.device.style.visibility);
      if (out.length > 60) { clearInterval(t); done(out); }
    }, 50);
  }));
  expect(samples.every((v) => v === 'visible')).toBe(true);
  await expect(shown().locator('#h')).toHaveText('Version three', { timeout: 20000 });
  await expect(shown().locator('#h')).toHaveCSS('color', 'rgb(10, 20, 30)');
  // A stylesheet-only save: the page keeps its state (a marker set in it survives) and picks up the new colour.
  await shown().locator('body').evaluate(() => { window.__kept = 'still here'; });
  fs.writeFileSync(css, '#h { color: rgb(200, 100, 50); }');
  await expect(shown().locator('#h')).toHaveCSS('color', 'rgb(200, 100, 50)', { timeout: 20000 });
  expect(await shown().locator('body').evaluate(() => window.__kept)).toBe('still here');
  await expect(page.locator('.pv-chips')).toContainText('Updated');
});

test('an empty page is never put on screen: FireDev says so and shows it once something is drawn', async ({ page }) => {
  await openWorkspace(page);
  const index = new URL('web/index.html', DIR);
  const original = fs.readFileSync(index, 'utf8');
  fs.writeFileSync(index, '<!doctype html><meta charset=utf-8><title>Empty</title><body></body>');
  await app(page, (pid) => window.__firedev.preview.open(pid), PID);
  await page.waitForFunction(() => window.__firedev.screen.ready, null, { timeout: 45000 });
  await expect(page.locator('.pv-status')).toContainText('nothing on it yet');
  expect(await app(page, () => window.__firedev.screen.device.style.visibility)).toBe('hidden');
  fs.writeFileSync(index, original);
  await expect.poll(() => app(page, () => window.__firedev.screen.device.style.visibility), { timeout: 20000 }).toBe('visible');
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

test('a build goes live by itself in three columns: what the agent does, the real page, and its real code replayed', async ({ page, request }) => {
  test.setTimeout(120000);
  await page.goto('/tv/?token=' + encodeURIComponent(tokens().tv));
  await page.waitForFunction(() => window.__firedev && window.__firedev.everOnline);
  expect(await route(page)).toBe('home');
  // Spoken from the phone; nobody touches the remote from here on.
  const r = await request.post('/api/command', { headers: { Authorization: 'Bearer ' + tokens().voice },
    data: { name: 'voice_command', args: { text: 'Build a landing page DEMO', project: 'demo' } } });
  expect((await r.json()).ok).toBe(true);
  await expect.poll(() => route(page), { timeout: 45000 }).toBe('preview');
  await expect(page.locator('.preview-screen.pv-3col')).toBeVisible();
  // The page is only shown once it has really loaded and drawn something.
  await page.waitForFunction(() => window.__firedev.screen.ready && window.__firedev.screen.device.style.visibility === 'visible', null, { timeout: 30000 });
  const shown = () => page.locator('iframe.pv-frame:not(.pv-next)').contentFrame();
  await expect(shown().locator('h1')).toHaveText('Pulse');
  // First look: the page takes the whole screen for a moment, labelled, then hands back to the columns.
  await expect(page.locator('.preview-screen.pv-reveal .pv-reveal-label')).toContainText('First look', { timeout: 5000 });
  await expect(page.locator('.code-panel')).toBeHidden();
  await page.evaluate(() => {
    window.__reveals = []; window.__cams = [];
    const seen = (list, t) => { if (t && list[list.length - 1] !== t) list.push(t); };
    setInterval(() => {
      const l = document.querySelector('.pv-reveal .pv-reveal-label');
      seen(window.__reveals, l && l.textContent);
      const c = document.querySelector('.pv-cam.on');
      seen(window.__cams, c && c.textContent);
    }, 150);
  });
  await expect(page.locator('.preview-screen.pv-reveal')).toHaveCount(0, { timeout: 25000 });
  // Live code: a replay of the agent's real edit, labelled as one, typing the real lines.
  await expect(page.locator('.cp-status.replay .cp-chip')).toHaveText('Replay', { timeout: 20000 });
  await expect(page.locator('.cp-status.replay')).toContainText('Codex’s edit to');
  await expect(page.locator('.code-panel .cp-row.typed').first()).toBeVisible();
  const typed = await page.locator('.code-panel .cp-row.typed .cp-tx').allTextContents();
  const real = ['<!doctype html>', '<title>Pulse</title>', '<h1>Pulse</h1>', '<p>Track every heartbeat of your app.</p>',
    'body { font: 20px system-ui; }', 'h1 { font-size: 64px; }', '<section class="features">Fast. Private. Beautiful.</section>', 'fixed'];
  for (const t of typed.map((x) => x.trim()).filter(Boolean)) expect(real.some((line) => line.startsWith(t))).toBe(true);
  // The feature section the agent adds later appears in the same page, without leaving the live view.
  await expect(shown().locator('.features')).toHaveText('Fast. Private. Beautiful.', { timeout: 40000 });
  // The camera followed that edit to the section it added, and said so.
  await expect.poll(() => page.evaluate(() => window.__cams), { timeout: 20000 }).toContain('Updating the features section');
  // When the agent is done, nothing types: the panel says so.
  await page.waitForFunction(() => { const p = window.__firedev.projects.demo; return p && !p.task; }, null, { timeout: 60000 });
  // Done: the finished page got the whole screen once more, and then the split came back.
  await expect.poll(() => page.evaluate(() => window.__reveals.some((t) => t.startsWith('Done'))), { timeout: 15000 }).toBe(true);
  await expect(page.locator('.preview-screen.pv-reveal')).toHaveCount(0, { timeout: 15000 });
  await expect(page.locator('.cp-status.done')).toBeVisible({ timeout: 20000 });
  await expect(page.locator('.code-panel.is-typing')).toHaveCount(0);
});

test('the gateway refuses anyone without a ticket from a paired TV', async ({ request }) => {
  const r = await request.get(GATEWAY + '/');
  expect(r.status()).toBe(403);
  const bad = await request.post('/api/command', { headers: { Authorization: 'Bearer ' + tokens().voice }, data: { name: 'preview_open', args: { project: PID } } });
  expect((await bad.json()).ok).toBe(false);
});
