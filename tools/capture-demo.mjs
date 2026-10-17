// Drives the hackathon demo through the real TV interface (browser at 1920×1080) against a running FireDev service
// with the real coding agent: a phone-microphone client speaks each line (live partial transcripts), the remote
// approves the deployment, and a screenshot is taken at every beat.
//
//   node --experimental-websocket tools/capture-demo.mjs --tv-token T --voice-token V [--out docs/screens] [--server http://127.0.0.1:8130]
import { chromium } from '@playwright/test';
import fs from 'node:fs';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const server = arg('server', 'http://127.0.0.1:8130');
const out = arg('out', 'docs/screens');
const tvToken = arg('tv-token');
const voiceToken = arg('voice-token');
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

const t0 = Date.now();
const startedAt = t0 / 1000;
const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6) + 's', ...a);
const shot = async (name) => { await page.screenshot({ path: `${out}/${name}.png` }); log('shot', name); };
const snap = () => page.evaluate(() => { const a = window.__firedev; const p = a && a.project(); return p ? { last: p.last_task, task: p.task, build: p.build, tests: p.tests, deploy: p.deploy, approvals: a.approvals.length, route: a.route.name, changes: p.changes.files.length } : null; });
async function until(fn, what, timeout = 900000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const s = await snap();
    if (s && fn(s)) return s;
    if (s && !s.task && s.last && s.last.status === 'failed' && s.last.started > startedAt) throw new Error('task failed: ' + s.last.error);
    await page.waitForTimeout(400);
  }
  throw new Error('timed out waiting for ' + what);
}

// The phone: a voice-role WebSocket client that streams words like speech recognition does.
const ws = new WebSocket(server.replace(/^http/, 'ws') + '/ws?token=' + encodeURIComponent(voiceToken));
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
async function speak(text) {
  log('🎙', text);
  ws.send(JSON.stringify({ op: 'voice', phase: 'listening', source: 'voice' }));
  const words = text.split(' ');
  for (let i = 1; i <= words.length; i++) {
    ws.send(JSON.stringify({ op: 'voice', phase: 'partial', text: words.slice(0, i).join(' '), source: 'voice' }));
    await page.waitForTimeout(170);
    if (i === Math.ceil(words.length * 0.6)) await shot('voice-' + text.split(' ')[0].toLowerCase() + '-listening');
  }
  ws.send(JSON.stringify({ op: 'voice', phase: 'final', text, source: 'voice' }));
  await page.waitForTimeout(700);
}

await page.goto(server + '/tv/?token=' + encodeURIComponent(tvToken));
await page.waitForTimeout(2500);
if ((await page.evaluate(() => window.__firedev.route.name)) !== 'home') { await page.keyboard.press('Escape'); await page.waitForTimeout(600); }
await shot('01-home');

await speak('Open my Android project.');
await until((s) => s.route === 'workspace', 'workspace');
await page.waitForTimeout(1500);
await shot('02-workspace-ready');

await speak('Why is the build failing?');
await shot('03-understood');
await until((s) => s.task && s.task.status === 'running' && !/Asking|Waiting to start/.test(s.task.status_text || ''), 'agent working');
await page.waitForTimeout(1200);
await shot('04-agent-investigating');
await until((s) => s.build && s.build.state === 'failed', 'build failed');
await page.waitForTimeout(800);
await shot('05-build-failed');
await until((s) => !s.task, 'investigation done');
await page.waitForTimeout(1200);
await shot('06-diagnosis');

await speak('Fix it and run the tests.');
await until((s) => s.task && /Editing/.test(s.task.status_text || ''), 'editing', 300000).catch(() => {});
await shot('07-agent-editing');
await until((s) => s.tests && s.tests.state === 'running', 'tests running', 600000).catch(() => {});
await shot('08-tests-running');
await until((s) => !s.task && s.tests.state === 'passed', 'tests passed');
await page.waitForTimeout(1200);
await shot('09-fixed-tests-pass');

// Review the diff with the remote: focus the Changes tile and open it.
await page.evaluate(() => window.__firedev.focus.focus('changes'));
await page.waitForTimeout(400);
await page.keyboard.press('Enter');
await page.waitForTimeout(1800);
await shot('10-diff');
await page.keyboard.press('ArrowDown');
await page.waitForTimeout(1200);
await shot('11-diff-second-file');
await page.keyboard.press('Escape');
await page.waitForTimeout(600);

await speak('Build the app.');
await until((s) => s.build && s.build.state === 'running', 'build running', 60000).catch(() => {});
await shot('12-building');
await until((s) => s.build && s.build.state === 'passed' && !s.task, 'build passed');
await page.waitForTimeout(1600);
await shot('13-build-complete');
await page.keyboard.press('Enter');
await page.waitForTimeout(500);

await speak('Deploy the preview.');
await until((s) => s.approvals > 0, 'approval');
await page.waitForTimeout(900);
await shot('14-approval');
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(400);
await shot('15-approval-approve-focused');
await page.keyboard.press('Enter');
await until((s) => s.deploy && s.deploy.state === 'passed', 'deploy');
await page.waitForTimeout(1800);
await shot('16-deployment-complete');
await page.keyboard.press('Enter');
await page.waitForTimeout(600);

await page.evaluate(() => window.__firedev.navigate('activity', { project: window.__firedev.route.project }));
await page.waitForTimeout(800);
await shot('17-activity');
await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp');
await page.waitForTimeout(300);
await page.keyboard.press('Enter');
await page.waitForTimeout(1200);
await shot('18-terminal-output');

ws.close();
await browser.close();
log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors');
log('DEMO UI RUN COMPLETE');
