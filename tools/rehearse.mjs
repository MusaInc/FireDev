// Demo rehearsal: drives a FireDev instance through a spoken script with the real agent, watching the TV UI at
// 1920×1080 the whole time with no remote presses. Records what the TV showed and said, with timestamps, plus
// screenshots, into <out>/report.json and <out>/timeline.txt.
//
//   node tools/rehearse.mjs <base> <tokens.json> <out> <script.json>
//
// tokens.json: {"tv": "<TV token>", "voice": "<voice token>"}. script.json: [{"say": "...", "wait": "done"|seconds,
// "max": seconds}]. Speech is recorded, not played: the page's speech engine is replaced by one that takes about as
// long as the TV's voice would.
import { chromium } from '@playwright/test';
import fs from 'node:fs';

const [,, base, tokFile, out, scriptFile] = process.argv;
if (!scriptFile) { console.error('usage: node tools/rehearse.mjs <base> <tokens.json> <out> <script.json>'); process.exit(2); }
const tok = JSON.parse(fs.readFileSync(tokFile, 'utf8'));
const script = JSON.parse(fs.readFileSync(scriptFile, 'utf8'));
fs.mkdirSync(out, { recursive: true });

// REHEARSE_VIDEO=1 also records the TV as <out>/video/*.webm (1920×1080); timeline times match the video's.
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  ...(process.env.REHEARSE_VIDEO ? { recordVideo: { dir: `${out}/video`, size: { width: 1920, height: 1080 } } } : {}) });
const tv = await ctx.newPage();
await tv.addInitScript(() => {
  window.__spoken = [];
  const listeners = [];
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  const synth = {
    speak(u) {
      window.__spoken.push({ t: Date.now(), text: u.text });
      setTimeout(() => u.onstart && u.onstart(), 30);
      const ms = 600 + u.text.split(/\s+/).length * 330; // about the TV's speaking rate
      u._timer = setTimeout(() => u.onend && u.onend(), ms);
      listeners.push(u);
    },
    cancel() { listeners.splice(0).forEach((u) => { clearTimeout(u._timer); u.onerror && u.onerror({ error: 'interrupted' }); }); },
    getVoices: () => [],
  };
  // speechSynthesis is a read-only accessor: plain assignment would be ignored.
  Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
});
const errors = [];
tv.on('pageerror', (e) => errors.push(String(e)));

const t0 = Date.now();
const sec = () => Math.round((Date.now() - t0) / 100) / 10;
const timeline = [];
const log = (what) => { const line = `${String(sec()).padStart(6)}s  ${what}`; timeline.push(line); console.log(line); };
let shots = 0;
// Each screenshot is logged with its time: tools/cut-demo.mjs finds them in the video to sync it (the recording's clock
// runs slow when the machine is busy).
const shot = async (name) => { const f = `${String(++shots).padStart(3, '0')}-${name}.png`; log(`📷 ${f}`); await tv.screenshot({ path: `${out}/${f}` }); return f; };

await tv.goto(base + '/tv/?token=' + encodeURIComponent(tok.tv));
await tv.waitForTimeout(400);
await shot('splash');
await tv.waitForFunction(() => window.__firedev && window.__firedev.everOnline, null, { timeout: 30000 });
await tv.waitForTimeout(2500);
log('TV online on ' + (await tv.evaluate(() => window.__firedev.route.name)));
await shot('home');

const state = () => tv.evaluate(() => {
  const a = window.__firedev, s = a.screen, pid = a.route.project || a.focusProject;
  const p = pid && a.projects[pid];
  const pv = pid && a.preview.state(pid);
  const q = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim().replace(/\s+/g, ' ') : null; };
  return {
    route: a.route.name, project: pid || null,
    task: p && p.task ? p.task.title + ' · ' + p.task.status : null,
    lastTask: p && p.last_task ? p.last_task.status : null,
    preview: pv ? pv.status : null,
    shown: s && s.device ? s.device.style.visibility !== 'hidden' && !!s.ready : null,
    cols: document.querySelector('.pv-3col') ? 3 : null,
    now: q('.now-verb') ? q('.now-verb') + ' ' + (q('.now-object') || '') : null,
    stage: q('.rail-node.current .rail-label'),
    code: q('.cp-status'),
    tests: p && p.tests ? (p.tests.state + (p.tests.total ? ` ${p.tests.passed}/${p.tests.total}` : '')) : null,
    codeEvents: (a.code[pid] || []).length,
    cam: q('.pv-cam.on'),
    scroll: (() => { try { const f = document.querySelector('iframe.pv-frame:not(.pv-next)'); return f ? Math.round(f.contentWindow.scrollY / 50) * 50 : null; } catch (e) { return 'x'; } })(),
    spoken: window.__spoken.length,
  };
});

const report = { base, started: new Date(t0).toISOString(), steps: [], errors };
let spokenSeen = 0;
let prev = {};
for (const [i, step] of script.entries()) {
  const at = sec();
  log(`SAY “${step.say}”`);
  const r = await fetch(base + '/api/command', { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + tok.voice },
    body: JSON.stringify({ name: 'voice_command', args: { text: step.say } }) });
  const reply = await r.json();
  log('  → ' + (reply.message || reply.title || JSON.stringify(reply)).slice(0, 160));
  const rec = { say: step.say, at, reply: reply.message || null, firsts: {}, shots: [] };
  const max = step.max || 600;
  let sawTask = false, doneAt = 0, lastShot = 0;
  while (sec() - at < max) {
    await tv.waitForTimeout(700);
    const st = await state();
    const spoken = await tv.evaluate((n) => window.__spoken.slice(n), spokenSeen);
    spokenSeen += spoken.length;
    spoken.forEach((s) => log(`  🔊 ${s.text}`));
    if (spoken.length) fs.writeFileSync(`${out}/timeline.txt`, timeline.join('\n') + '\n');
    for (const k of Object.keys(st)) {
      if (k === 'spoken' || k === 'codeEvents') continue;
      if (st[k] !== prev[k]) log(`  ${k}: ${st[k]}`);
    }
    if (st.codeEvents !== prev.codeEvents) log(`  code events: ${st.codeEvents}`);
    const mark = (name, cond) => { if (cond && rec.firsts[name] === undefined) { rec.firsts[name] = Math.round((sec() - at) * 10) / 10; log(`  ★ ${name} after ${rec.firsts[name]}s`); return true; } return false; };
    mark('task started', st.task);
    mark('workspace or preview on screen', st.route !== 'home' && st.route !== 'center');
    const live = mark('website on screen', st.route === 'preview' && st.shown);
    const three = mark('three panels', st.cols === 3);
    const typing = mark('code replaying', /Replay/.test(st.code || ''));
    if (live || three || typing) rec.shots.push(await shot(`s${i + 1}-${live ? 'live' : three ? '3col' : 'code'}`));
    if (Date.now() - lastShot > (st.route === 'preview' ? 6000 : 15000)) { rec.shots.push(await shot(`s${i + 1}-${st.route}`)); lastShot = Date.now(); }
    prev = st;
    if (st.task) sawTask = true;
    if (step.wait === 'done' && sawTask && !st.task && !doneAt) { doneAt = Date.now(); mark('task finished', true); }
    if (doneAt && Date.now() - doneAt > (step.settle || 15000)) break;
    if (typeof step.wait === 'number' && sec() - at >= step.wait) break;
  }
  const st = await state();
  rec.end = st;
  // FireDev's own timings for the request: heard → dispatched → Codex's first event → first file → … (ms).
  rec.timings = await tv.evaluate(() => { const a = window.__firedev, p = a.projects[a.route.project || a.focusProject];
    const t = p && (p.task || p.last_task); return t ? t.timings : null; });
  log('  timings: ' + JSON.stringify(rec.timings));
  fs.writeFileSync(`${out}/timeline.txt`, timeline.join('\n') + '\n');
  rec.seconds = Math.round((sec() - at) * 10) / 10;
  rec.shots.push(await shot(`s${i + 1}-end`));
  report.steps.push(rec);
}
report.spoken = await tv.evaluate(() => window.__spoken.map((s) => s.text));
report.finished = new Date().toISOString();
fs.writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2));
fs.writeFileSync(`${out}/timeline.txt`, timeline.join('\n') + '\n');
log('page errors: ' + (errors.length ? errors.join(' | ') : 'none'));
await ctx.close(); // finishes the video file
await browser.close();
