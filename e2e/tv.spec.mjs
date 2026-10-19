// The TV interface driven only by remote keys (arrows, OK = Enter, Back = Escape, Menu = m), against a real FireDev
// service: SSH to localhost, tmux, and the scripted stand-in agent. Runs at 1920×1080.
import { expect, test } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

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
    if ((await route(page)) !== 'home' && (await route(page)) !== 'projects') await press(page, 'Escape');
    if ((await route(page)) === 'home') await toProjects(page);
    await expect.poll(() => focused(page)).toBe('project-' + PID);
    await press(page, 'Enter');
  }
  await waitFor(page, (pid) => { const p = window.__firedev.projects[pid]; return p && p.connection === 'connected' && p.agent.state === 'ready'; }, PID);
}
async function toProjects(page) {
  // Home is the voice screen; the projects list sits below "Start talking".
  await expect.poll(() => focused(page)).toBe('talk');
  await press(page, 'ArrowDown');
  expect(await focused(page)).toBe('projects');
  await press(page, 'Enter');
  expect(await route(page)).toBe('projects');
}
async function idle(page) {
  await waitFor(page, (pid) => { const p = window.__firedev.projects[pid]; return p && !p.task && !(p.queue || []).length; }, PID, 60000);
}

test.describe.configure({ mode: 'serial' });

test('home is the voice screen: an orb, one sentence and Start talking, with the projects one step away', async ({ page }) => {
  await open(page);
  expect(await route(page)).toBe('home');
  await expect(page.locator('.home .orb')).toBeVisible();
  await expect(page.locator('.home-line')).toContainText('What shall we build?');
  expect(await focused(page)).toBe('talk');
  await expect(page.locator('.btn.talk.is-focused')).toHaveCSS('background-color', 'rgb(244, 241, 234)');
  await expect(page.locator('.project-card')).toHaveCount(0);
  await toProjects(page);
  await expect(page.locator('.project-card')).toContainText('Fixture App');
  await expect(page.locator('.project-card')).toContainText('test-box');
  await expect(page.locator('.project-card')).toContainText('Codex');
  expect(await focused(page)).toBe('project-' + PID);
  await press(page, 'ArrowRight');
  expect(await focused(page)).toBe('phone-mic');
  await press(page, 'ArrowLeft');
  expect(await focused(page)).toBe('project-' + PID);
  await press(page, 'Escape');
  expect(await route(page)).toBe('home');
});

test('OK opens the project: SSH, tmux and agent come up and the workspace shows it', async ({ page }) => {
  await open(page);
  await toProjects(page);
  await press(page, 'Enter');
  expect(await route(page)).toBe('workspace');
  await toWorkspace(page);
  await expect(page.locator('.topbar')).toContainText('Codex · ready');
  await expect(page.locator('.topbar')).toContainText('test-box');
  await expect(page.locator('.task-card')).toContainText('What should Codex do?');
  expect(await focused(page)).toBe('task');
  // Back returns to the voice home.
  await press(page, 'Escape');
  expect(await route(page)).toBe('home');
  expect(await focused(page)).toBe('talk');
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

// The installed CLI, pointed at the test service's config and admin token.
function cli(...args) {
  const tmp = new URL('./.tmp/', import.meta.url).pathname;
  try {
    return { ok: true, out: execFileSync(new URL('../firedev', import.meta.url).pathname, args,
      { env: { ...process.env, FIREDEV_HOME: tmp + 'home', FIREDEV_CONFIG: tmp + 'firedev.yaml' }, encoding: 'utf8', stdio: 'pipe' }) };
  } catch (e) { return { ok: false, out: String(e.stderr || '') + String(e.stdout || '') }; }
}
async function unpairedTv(page) {
  await page.goto('/tv/?token=');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/tv/');
  await expect(page.locator('.code-digits')).toBeVisible();
  return page.evaluate(() => window.__firedev.pairingCode);
}

test('the pairing code stays the same through heartbeats and reconnects, and shows its expiry', async ({ page }) => {
  const code = await unpairedTv(page);
  await expect(page.locator('.pair-countdown')).toHaveText(/valid for (10:00|9:5\d) more/);
  await page.waitForTimeout(20000); // several heartbeats; the old server dropped the TV (and its code) after 12 s
  expect(await page.evaluate(() => window.__firedev.pairingCode)).toBe(code);
  await page.evaluate(() => window.__firedev.link.reconnectNow());
  await waitFor(page, () => window.__firedev.status === 'pairing' && !window.__firedev.linkDown);
  expect(await page.evaluate(() => window.__firedev.pairingCode)).toBe(code);
  await page.reload(); // the app restarting keeps it too
  await expect(page.locator('.code-digits')).toBeVisible();
  expect(await page.evaluate(() => window.__firedev.pairingCode)).toBe(code);
  await expect(page.locator('.pair-countdown')).toHaveText(/valid for 9:[34]\d more/);
});

test('a wrong code is refused by the CLI and the TV keeps waiting; the right one pairs and opens the app', async ({ page }) => {
  const code = await unpairedTv(page);
  const wrong = cli('pair', code === '000000' ? '111111' : '000000');
  expect(wrong.ok).toBe(false);
  expect(wrong.out).toContain('No TV is showing that code');
  expect(await page.evaluate(() => window.__firedev.pairingCode)).toBe(code);
  const right = cli('pair', code, '--name', 'CLI TV');
  expect(right.ok).toBe(true);
  expect(right.out).toContain('Paired CLI TV');
  await waitFor(page, () => window.__firedev.route.name === 'home' || window.__firedev.route.name === 'workspace');
  expect(await page.evaluate(() => window.__firedev.client.name)).toBe('CLI TV');
});

test('an expired code offers a new one from the remote', async ({ page, request }) => {
  const code = await unpairedTv(page);
  await page.evaluate(() => { window.__firedev.pairingExpiresAt = Date.now(); }); // as if 10 minutes passed
  await expect(page.locator('.t-title')).toHaveText('That pairing code has expired');
  await expect.poll(() => focused(page)).toBe('new-code');
  await press(page, 'Enter');
  await expect(page.locator('.code-digits')).toBeVisible();
  const fresh = await page.evaluate(() => window.__firedev.pairingCode);
  expect(fresh).not.toBe(code);
  const old = await request.post('/api/pair', { headers: { Authorization: 'Bearer ' + tokens().admin }, data: { code } });
  expect(old.status()).toBe(404); // the replaced code no longer pairs
  const r = await request.post('/api/pair', { headers: { Authorization: 'Bearer ' + tokens().admin }, data: { code: fresh, name: 'Renewed TV' } });
  expect(r.ok()).toBeTruthy();
  await waitFor(page, () => window.__firedev.route.name === 'home' || window.__firedev.route.name === 'workspace');
});

// A TV WebView whose CSS viewport is smaller than 1920×1080 (960×540 on most 1080p TVs) must show the whole
// pairing screen scaled down, not a 1:1 corner of it.
test('the pairing screen fits a small mobile-style TV viewport inside the safe area', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, isMobile: true });
  const page = await ctx.newPage();
  await page.goto('/tv/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/tv/');
  await expect(page.locator('.code-digits')).toBeVisible();
  const outside = await page.evaluate(() => {
    const w = window.visualViewport.width, h = window.visualViewport.height, bad = [];
    for (const el of document.querySelectorAll('#screen .t-title, #screen .code-digits span, #screen .terminal, #screen .t-body, #screen .t-meta')) {
      const r = el.getBoundingClientRect();
      if (r.left < w * 0.05 || r.top < h * 0.05 || r.right > w * 0.95 || r.bottom > h * 0.95) bad.push(el.className + ' ' + JSON.stringify([r.left, r.top, r.right, r.bottom]));
    }
    return bad;
  });
  expect(outside).toEqual([]);
  await ctx.close();
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

test('the phone microphone page sends words that the TV shows, and can never approve', async ({ browser, request }) => {
  const tv = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await open(tv);
  await toWorkspace(tv);
  await idle(tv);
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto('/voice#t=' + encodeURIComponent(tokens().voice));
  await expect(phone.locator('#conn')).toContainText('Connected');
  await phone.fill('#text', 'Run the tests');
  await phone.click('button.send');
  await expect(tv.locator('.voice')).toContainText('Understood');
  await expect(tv.locator('.voice')).toContainText('from your phone');
  await expect(tv.locator('.result')).toBeVisible({ timeout: 30000 });
  await tv.keyboard.press('Enter');
  // Ask for a deploy from the phone: the page says to approve on the TV, and the approval waits there.
  await phone.fill('#text', 'Deploy the preview');
  await phone.click('button.send');
  await expect(tv.locator('.approval')).toBeVisible();
  await expect(phone.locator('#approval')).toBeVisible();
  const denied = await api(request, 'approve_action', {}, tokens().voice);
  expect(denied.ok).toBeFalsy();
  await tv.keyboard.press('Enter'); // Reject is focused
  await expect(tv.locator('.approval')).toHaveCount(0);
  await phone.close();
  await tv.close();
});

test('nobody touches the remote: what you say on the phone moves the TV to the right screen', async ({ browser }) => {
  const tv = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await open(tv);
  expect(await route(tv)).toBe('home');
  await idle(tv);
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto('/voice#t=' + encodeURIComponent(tokens().voice));
  await expect(phone.locator('#conn')).toContainText('Connected');
  // Home knows the phone is listening and says so.
  await expect(tv.locator('.home-status')).toContainText('Phone microphone');
  await expect(tv.locator('.home-sub')).toContainText('Talk on your phone');
  const say = async (text) => { await phone.fill('#text', text); await phone.click('button.send'); };
  await say('Show my projects');
  await expect.poll(() => route(tv)).toBe('projects');
  await say('Go home');
  await expect.poll(() => route(tv)).toBe('home');
  // The words show on home itself (no sheet over it).
  await say('Show me what changed');
  await expect.poll(() => route(tv)).toBe('diff');
  expect(await tv.evaluate(() => window.__firedev.route.project)).toBe(PID);
  await say('Go home');
  await expect.poll(() => route(tv)).toBe('home');
  await phone.close();
  await expect(tv.locator('.home-status')).not.toContainText('Phone microphone');
  await tv.close();
});

// Alexa requests signed with the test CA's key (e2e/server.py trusts it in place of Amazon's), sent over HTTP.
async function alexa(request, type, intent, slots) {
  const req = { type, requestId: 'amzn1.echo-api.request.' + crypto.randomUUID(), timestamp: new Date().toISOString().replace(/\.\d+Z$/, 'Z'), locale: 'en-GB' };
  if (intent) req.intent = { name: intent, slots: slots || {} };
  const system = { application: { applicationId: 'amzn1.ask.skill.e2e' }, user: { userId: 'amzn1.ask.account.E2E' }, device: { deviceId: 'fire-tv' } };
  const body = JSON.stringify({ version: '1.0', context: { System: system }, request: req });
  const key = fs.readFileSync(new URL('./.tmp/alexa-key.pem', import.meta.url));
  const sig = crypto.sign('sha256', Buffer.from(body), key).toString('base64');
  const r = await request.post('/alexa', { headers: { 'Content-Type': 'application/json', SignatureCertChainUrl: 'https://s3.amazonaws.com/echo.api/echo-api-cert.pem', 'Signature-256': sig }, data: body });
  return { status: r.status(), say: r.ok() ? (await r.json()).response.outputSpeech.text : null };
}

test('Alexa: unsigned requests are refused, linking needs OK held on the TV, then tasks, results and approvals', async ({ page, request }) => {
  await open(page);
  await toWorkspace(page);
  await idle(page);
  const unsigned = await request.post('/alexa', { data: { request: { type: 'LaunchRequest' } } });
  expect(unsigned.status()).toBe(400);

  // An unknown Alexa account gets a code; the TV asks to link it.
  const first = await alexa(request, 'LaunchRequest');
  const code = /code ((?:\d ){3}\d)/.exec(first.say)[1].replace(/ /g, '');
  await expect(page.locator('.approval')).toContainText('Link a voice assistant');
  await expect(page.locator('.approval .command')).toHaveText('Code ' + code.split('').join(' '));
  expect((await alexa(request, 'IntentRequest', 'RunTestsIntent')).say).toContain('look at your TV');
  await press(page, 'ArrowRight');
  await expect(page.locator('[data-focus-id="approve"]')).toContainText('Hold OK to link');
  await press(page, 'Enter');
  await page.waitForTimeout(500);
  await expect(page.locator('.approval')).toBeVisible();
  await page.keyboard.down('Enter');
  await page.waitForTimeout(2300);
  await page.keyboard.up('Enter');
  await expect(page.locator('.approval')).toHaveCount(0);

  // Linked: a task starts at once, the TV shows it came from Alexa, and the result can be asked for.
  expect((await alexa(request, 'LaunchRequest')).say).toContain('FireDev is ready.');
  const started = await alexa(request, 'IntentRequest', 'RunTestsIntent');
  expect(started.say).toContain('Run the tests.');
  await expect(page.locator('.voice')).toContainText('from Alexa');
  await expect(page.locator('.result')).toBeVisible({ timeout: 30000 });
  await page.keyboard.press('Enter');
  // The fixture's tests fail on purpose (it's the broken demo app); Alexa says how many passed.
  expect((await alexa(request, 'IntentRequest', 'ProgressIntent')).say).toMatch(/^Run the tests failed\. \d+ of \d+ tests passed/);
  expect((await alexa(request, 'IntentRequest', 'ResultIntent')).say).toMatch(/^Run the tests failed\. \d+ of \d+ tests passed\. The details are on your TV\.$/);

  // A deploy waits on the TV; saying "approve" to Alexa doesn't approve it.
  expect((await alexa(request, 'IntentRequest', 'DeployPreviewIntent')).say).toContain('approval on the TV');
  await expect(page.locator('.approval')).toBeVisible();
  expect((await alexa(request, 'IntentRequest', 'ApproveIntent')).say).toContain('Approvals stay on the TV');
  await page.waitForTimeout(500);
  await expect(page.locator('.approval')).toBeVisible();
  await page.keyboard.press('Enter'); // Reject is focused
  await expect(page.locator('.approval')).toHaveCount(0);
});
