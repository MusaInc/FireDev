// The TV interface driven only by remote keys (arrows, OK = Enter, Back = Escape, Menu = m), against a real FireDev
// service: SSH to localhost, tmux, and the scripted stand-in agent. Runs at 1920×1080.
import { expect, test } from '@playwright/test';
import fs from 'node:fs';

const tokens = () => JSON.parse(fs.readFileSync(new URL('./.tmp/tokens.json', import.meta.url)));
const PID = 'e2e';

async function open(page, token) {
  await page.goto('/tv/?token=' + encodeURIComponent(token || tokens().tv));
  await page.waitForFunction(() => window.__firedev && window.__firedev.everOnline);
}
const focused = (page) => page.evaluate(() => window.__firedev.focus.focusedId());
const route = (page) => page.evaluate(() => window.__firedev.route.name);
const snap = (page) => page.evaluate((pid) => window.__firedev.projects[pid], PID);
async function press(page, ...keys) {
  for (const k of keys) { await page.keyboard.press(k); await page.waitForTimeout(220); }
}
async function api(request, name, args, token) {
  const r = await request.post('/api/command', { headers: { Authorization: 'Bearer ' + (token || tokens().admin) }, data: { name, args } });
  return r.json();
}
async function waitFor(page, fn, arg, timeout = 30000) {
  await page.waitForFunction(fn, arg, { timeout, polling: 200 });
}
async function toWorkspace(page) {
  if ((await route(page)) !== 'workspace') {
    if ((await route(page)) !== 'home') await press(page, 'Escape');
    await expect.poll(() => focused(page)).toBe('project-' + PID);
    await press(page, 'Enter');
  }
  await waitFor(page, (pid) => { const p = window.__firedev.projects[pid]; return p && p.connection === 'connected' && p.agent.state === 'ready'; }, PID);
}
async function idle(page) {
  await waitFor(page, (pid) => { const p = window.__firedev.projects[pid]; return p && !p.task && !(p.queue || []).length; }, PID, 60000);
}

test.describe.configure({ mode: 'serial' });

test('home shows the project card with a visible focus and D-pad moves between cards', async ({ page }) => {
  await open(page);
  expect(await route(page)).toBe('home');
  await expect(page.locator('.project-card')).toContainText('Fixture App');
  await expect(page.locator('.project-card')).toContainText('test-box');
  await expect(page.locator('.project-card')).toContainText('Codex');
  expect(await focused(page)).toBe('project-' + PID);
  await expect(page.locator('.project-card.is-focused')).toHaveCSS('background-color', 'rgb(244, 241, 234)');
  await press(page, 'ArrowRight');
  expect(await focused(page)).toBe('phone-mic');
  await press(page, 'ArrowLeft');
  expect(await focused(page)).toBe('project-' + PID);
});

test('OK opens the project: SSH, tmux and agent come up and the workspace shows it', async ({ page }) => {
  await open(page);
  await press(page, 'Enter');
  expect(await route(page)).toBe('workspace');
  await toWorkspace(page);
  await expect(page.locator('.topbar')).toContainText('Codex · ready');
  await expect(page.locator('.topbar')).toContainText('test-box');
  await expect(page.locator('.task-card')).toContainText('What should Codex do?');
  expect(await focused(page)).toBe('task');
  // Back returns to the projects, with the card still selected.
  await press(page, 'Escape');
  expect(await route(page)).toBe('home');
  expect(await focused(page)).toBe('project-' + PID);
});

test('menu opens the voice sheet; a quick command runs the tests and shows the result', async ({ page }) => {
  await open(page);
  await toWorkspace(page);
  await press(page, 'm');
  await expect(page.locator('.voice')).toBeVisible();
  const chips = await page.locator('.voice .chip').allTextContents();
  expect(chips).toContain('Run the tests');
  const idx = chips.indexOf('Run the tests');
  for (let i = 0; i < idx; i++) await press(page, 'ArrowRight');
  expect(await page.locator('.voice .chip.is-focused').textContent()).toBe('Run the tests');
  await press(page, 'Enter');
  await expect(page.locator('.voice')).toContainText('Understood');
  await expect(page.locator('.result')).toContainText('Tests failed', { timeout: 30000 });
  await expect(page.locator('.result')).toContainText('2 of 3 passed');
  await expect(page.locator('.tile[data-focus-id="tests"]')).toContainText('2 / 3 passed');
  await press(page, 'Enter');
  await expect(page.locator('.result')).toHaveCount(0);
});

test('a level 2 action waits on the TV: Reject is focused first, Back does not dismiss, Approve runs it', async ({ page, request }) => {
  await open(page);
  await toWorkspace(page);
  await idle(page);
  const r = await api(request, 'voice_command', { text: 'Deploy the preview', project: PID }, tokens().voice);
  expect(r.message).toContain('approval');
  await expect(page.locator('.approval')).toBeVisible();
  await expect(page.locator('.approval .command')).toHaveText('./deploy.sh');
  await expect(page.locator('.approval')).toContainText('Publish the latest build to the preview server.');
  await expect(page.locator('.approval')).toContainText('Level 2 · System change');
  expect(await focused(page)).toBe('reject');
  await press(page, 'Escape');
  await expect(page.locator('.approval')).toBeVisible();
  // The phone can't approve it.
  const denied = await api(request, 'approve_action', {}, tokens().voice);
  expect(denied.ok).toBeFalsy();
  await press(page, 'ArrowRight');
  expect(await focused(page)).toBe('approve');
  await press(page, 'Enter');
  await expect(page.locator('.result')).toContainText('Deployment complete', { timeout: 30000 });
  await expect(page.locator('.result')).toContainText('All checks passed');
  await expect(page.locator('.result')).toContainText('Health check passed');
  await press(page, 'Enter');
});

test('rejecting with the remote cancels the action', async ({ page, request }) => {
  await open(page);
  await toWorkspace(page);
  await idle(page);
  await api(request, 'run_action', { project: PID, action: 'deploy_preview' }, tokens().tv);
  await expect(page.locator('.approval')).toBeVisible();
  await press(page, 'Enter');
  await expect(page.locator('.approval')).toHaveCount(0);
  await waitFor(page, (pid) => { const t = window.__firedev.projects[pid].last_task; return t && t.status === 'cancelled'; }, PID);
  await expect(page.locator('.task-card')).toContainText('stopped');
});

test('a level 3 action needs OK held for two seconds', async ({ page, request }) => {
  await open(page);
  await toWorkspace(page);
  await idle(page);
  await api(request, 'run_action', { project: PID, action: 'wipe' }, tokens().tv);
  await expect(page.locator('.approval.l3')).toBeVisible();
  await expect(page.locator('.approval')).toContainText('Strong confirmation required');
  await expect(page.locator('.approval .command')).toHaveText('rm -rf out reports');
  await press(page, 'ArrowRight');
  await expect(page.locator('[data-focus-id="approve"]')).toContainText('Hold OK to approve');
  // A short press does nothing.
  await press(page, 'Enter');
  await page.waitForTimeout(500);
  await expect(page.locator('.approval')).toBeVisible();
  // Holding OK approves.
  await page.keyboard.down('Enter');
  await page.waitForTimeout(2300);
  await page.keyboard.up('Enter');
  await expect(page.locator('.approval')).toHaveCount(0);
  await waitFor(page, (pid) => { const t = window.__firedev.projects[pid].last_task; return t && t.title === 'Wipe build outputs' && t.status === 'completed'; }, PID);
});

test('agent task: live status, file change, diff view with remote scrolling, then undo', async ({ page, request }) => {
  await open(page);
  await toWorkspace(page);
  await idle(page);
  await api(request, 'voice_command', { text: 'Fix it and run the tests', project: PID }, tokens().voice);
  await expect(page.locator('.task-card')).toContainText('Current task', { timeout: 15000 });
  await waitFor(page, (pid) => { const p = window.__firedev.projects[pid]; return p.last_task && p.last_task.status === 'completed' && !p.task; }, PID, 60000);
  await expect(page.locator('.task-card')).toContainText('Fixed the app file. Tests pass.');
  await expect(page.locator('.tile[data-focus-id="tests"]')).toContainText('3 / 3 passed');
  await expect(page.locator('.tile[data-focus-id="changes"]')).toContainText('1 file');
  // Focus the Changes tile with the D-pad and open it.
  await press(page, 'ArrowRight');
  for (let i = 0; i < 3 && (await focused(page)) !== 'changes'; i++) await press(page, 'ArrowDown');
  expect(await focused(page)).toBe('changes');
  await press(page, 'Enter');
  expect(await route(page)).toBe('diff');
  await expect(page.locator('.file')).toContainText('app.txt');
  await expect(page.locator('.dl.del')).toContainText('broken');
  await expect(page.locator('.dl.add')).toContainText('fixed');
  await press(page, 'ArrowRight');
  expect(await focused(page)).toBe('pane');
  await press(page, 'Escape');
  expect(await focused(page)).toBe('file-src/app.txt');
  await press(page, 'Escape');
  expect(await route(page)).toBe('workspace');
  const undo = await api(request, 'voice_command', { text: 'Undo that change', project: PID }, tokens().voice);
  expect(undo.ok).toBeTruthy();
  await expect(page.locator('.tile[data-focus-id="changes"]')).toContainText('No changes');
});

test('activity lists every step; OK shows the raw terminal output', async ({ page }) => {
  await open(page);
  await toWorkspace(page);
  await press(page, 'Enter');
  expect(await route(page)).toBe('activity');
  await expect(page.locator('.row').first()).toBeVisible();
  const titles = await page.locator('.row .title').allTextContents();
  expect(titles.some((t) => /Tests: \d \/ 3 passed/.test(t))).toBeTruthy();
  // The newest row is focused; walk up to a command row that has output.
  for (let i = 0; i < 40; i++) {
    const hasOutput = await page.evaluate(() => { const el = window.__firedev.focus.focusedEl(); return !!(el && el.querySelector('.key')); });
    if (hasOutput) break;
    await press(page, 'ArrowUp');
  }
  await press(page, 'Enter');
  await expect(page.locator('.code')).not.toHaveText('Loading…');
  await expect(page.locator('.code')).not.toHaveText('No output.');
  await press(page, 'Escape');
  await expect(page.locator('.code')).toHaveCount(0);
  await press(page, 'Escape');
  expect(await route(page)).toBe('workspace');
});

test('nothing important sits outside the TV safe area', async ({ page }) => {
  await open(page);
  await toWorkspace(page);
  const outside = await page.evaluate(() => {
    const bad = [];
    for (const el of document.querySelectorAll('#screen .focusable, #screen .t-heading, #screen .t-title, #screen .pill')) {
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      if (r.left < 48 || r.top < 32 || r.right > 1920 - 48 || r.bottom > 1080 - 32) bad.push(el.className + ' ' + JSON.stringify([r.left, r.top, r.right, r.bottom]));
    }
    return bad;
  });
  expect(outside).toEqual([]);
});

test('an unpaired TV shows a pairing code and pairs from the CLI', async ({ page, request }) => {
  await page.goto('/tv/?token=');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/tv/');
  await expect(page.locator('.code-digits')).toBeVisible();
  const code = await page.evaluate(() => window.__firedev.pairingCode);
  expect(code).toMatch(/^\d{6}$/);
  await expect(page.locator('.terminal')).toHaveText('$ firedev pair ' + code);
  const r = await request.post('/api/pair', { headers: { Authorization: 'Bearer ' + tokens().admin }, data: { code, name: 'Test TV' } });
  expect(r.ok()).toBeTruthy();
  await waitFor(page, () => window.__firedev.route.name === 'home' || window.__firedev.route.name === 'workspace');
  expect(await page.evaluate(() => localStorage.getItem('firedev.token'))).toBeTruthy();
});

test('the TV reconnects after its connection drops and resyncs state', async ({ page }) => {
  await open(page);
  await toWorkspace(page);
  await page.evaluate(() => window.__firedev.link.reconnectNow());
  await waitFor(page, () => window.__firedev.status === 'online');
  await expect(page.locator('.toast')).toContainText('Back online');
  const p = await snap(page);
  expect(p.connection).toBe('connected');
});
