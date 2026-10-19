// Cuts a rehearsal recording (tools/rehearse.mjs with REHEARSE_VIDEO=1) into a short demo video.
//
//   node tools/cut-demo.mjs <rehearsal dir> <out.mp4> [target seconds, default 180]
//
// Nothing is staged: it plays the TV recording in order, in real time around every moment that matters (a spoken
// command, FireDev or Codex speaking, the switch to the live page, test results) and fast-forwards the waits in between,
// with a visible "Fast-forward N×" badge on every sped-up stretch. Captions show what was said to the TV; the title
// and end cards use the TV's own font and brand, and the end card's numbers come from the rehearsal's report.json.
import { chromium } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [,, dir, outFile, targetArg] = process.argv;
if (!outFile) { console.error('usage: node tools/cut-demo.mjs <rehearsal dir> <out.mp4> [seconds]'); process.exit(2); }
const TARGET = Number(targetArg || 180);
const INTRO = 4, OUTRO = 6, MAX_FF = 24;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const work = path.join(dir, 'cut');
fs.mkdirSync(work, { recursive: true });

const video = fs.readdirSync(path.join(dir, 'video')).filter((f) => f.endsWith('.webm')).map((f) => path.join(dir, 'video', f))[0];
const probe = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video], { encoding: 'utf8' });
const videoLength = Number(probe.stdout.trim());
const report = JSON.parse(fs.readFileSync(path.join(dir, 'report.json'), 'utf8'));

const events = fs.readFileSync(path.join(dir, 'timeline.txt'), 'utf8').split('\n').map((l) => {
  const m = l.match(/^\s*([\d.]+)s\s+(.*)$/);
  return m && { t: Number(m[1]), what: m[2] };
}).filter(Boolean);

// --- syncing the recording to the timeline ---------------------------------------------------------------------------
// The recording's clock runs slow when the machine is busy (by ~10% with Codex working), so timeline times can't be used
// as video times. Anchors [timeline s, video s] come from <dir>/sync.json, or are found by matching the rehearsal's
// logged screenshots against the video's frames.
function findAnchors() {
  const W = 384, H = 216, N = W * H, FPS = 4;
  const raw = spawnSync('ffmpeg', ['-v', 'error', '-i', video, '-vf', `fps=${FPS},scale=${W}:${H},format=gray`, '-f', 'rawvideo', '-'], { maxBuffer: 2e9 }).stdout;
  const frames = Math.floor(raw.length / N);
  const shots = events.map((e) => ({ t: e.t, f: (e.what.match(/^📷 (\S+\.png)$/) || [])[1] })).filter((s) => s.f && fs.existsSync(path.join(dir, s.f)));
  const step = Math.max(1, Math.floor(shots.length / 16));
  const found = [[0, 0]];
  for (const s of shots.filter((_, i) => i % step === 0)) {
    const img = spawnSync('ffmpeg', ['-v', 'error', '-i', path.join(dir, s.f), '-vf', `scale=${W}:${H},format=gray`, '-f', 'rawvideo', '-'], { maxBuffer: 1e8 }).stdout;
    const prev = found[found.length - 1][1];
    let best = Infinity, at = -1;
    for (let i = Math.max(0, Math.floor(prev * FPS)); i < Math.min(frames, Math.ceil((s.t * 1.4 + 15) * FPS)); i++) {
      let d = 0;
      for (let j = 0; j < N; j += 7) d += Math.abs(img[j] - raw[i * N + j]);
      if (d < best) { best = d; at = i / FPS; }
    }
    if (at >= prev && s.t > found[found.length - 1][0]) found.push([s.t, at]);
  }
  return found;
}
const syncFile = path.join(dir, 'sync.json');
const anchors = fs.existsSync(syncFile) ? JSON.parse(fs.readFileSync(syncFile, 'utf8')) : findAnchors();
const interp = (pts, x, from, to) => {
  let i = pts.findIndex((p) => p[from] > x);
  if (i === -1) i = pts.length - 1; else if (i === 0) i = 1;
  const [p, q] = [pts[i - 1], pts[i]];
  return p[to] + (x - p[from]) * (q[to] - p[to]) / ((q[from] - p[from]) || 1);
};
const toVideo = (t) => Math.min(videoLength, Math.max(0, interp(anchors, t, 0, 1)));
const length = interp(anchors, videoLength, 1, 0); // the timeline's length
console.log('sync anchors [timeline, video]:', anchors.map(([a, b]) => `${a.toFixed(1)}→${b.toFixed(1)}`).join(' '));

// --- moments worth real time, from the rehearsal timeline ----------------------------------------------------------
const keep = [[0, 6]]; // splash and home
const says = [];
for (const e of events) {
  if (e.what.startsWith('SAY ')) { keep.push([e.t - 1.5, e.t + 7]); says.push({ t: e.t, text: e.what.slice(4) }); }
  else if (e.what.includes('🔊')) keep.push([e.t - 0.5, e.t + Math.min(9, 2 + e.what.split(/\s+/).length * 0.33)]);
  else if (e.what.includes('★')) keep.push([e.t - 1, e.t + 6]);
  else if (/tests: passed|tests: failed/.test(e.what)) keep.push([e.t - 1, e.t + 4]);
  else if (/(code events|now|stage): /.test(e.what)) keep.push([e.t - 0.3, e.t + 1.2]); // a glimpse of each new step
}
keep.push([length - 10, length]);
keep.sort((a, b) => a[0] - b[0]);
let merged = [];
for (const [a0, b0] of keep) {
  const a = Math.max(0, a0), b = Math.min(length, b0);
  const last = merged[merged.length - 1];
  if (last && a <= last[1] + 1.5) last[1] = Math.max(last[1], b); else merged.push([a, b]);
}
// Too much real time for the target? Shorten the longest kept stretches (never the spoken commands).
const budget = TARGET - INTRO - OUTRO - 8;
const realTime = () => merged.reduce((s, [a, b]) => s + b - a, 0);
while (realTime() > budget * 0.8) {
  const i = merged.reduce((bi, r, j) => (r[1] - r[0] > merged[bi][1] - merged[bi][0] && !says.some((s) => s.t >= r[0] && s.t <= r[1]) ? j : bi), 0);
  const r = merged[i];
  if (r[1] - r[0] < 4) break;
  r[1] -= Math.min(2, r[1] - r[0] - 3);
}
// Segments: kept stretches at 1×, the gaps between them fast-forwarded to fit the target.
const segs = [];
let t = 0;
for (const [a, b] of merged) {
  if (a > t + 0.2) segs.push({ a: t, b: a, k: 0 });
  segs.push({ a, b, k: 1 });
  t = b;
}
if (t < length - 0.2) segs.push({ a: t, b: length, k: 0 });
const gapTime = segs.filter((s) => !s.k).reduce((s, g) => s + g.b - g.a, 0);
const k = Math.min(MAX_FF, Math.max(2, Math.ceil(gapTime / Math.max(1, budget - realTime()))));
segs.forEach((s) => { if (!s.k) s.k = Math.min(k, Math.max(2, Math.ceil((s.b - s.a) / 1.5))); });
for (const s of segs) s.out = (s.b - s.a) / s.k;
let o = INTRO;
for (const s of segs) { s.o = o; o += s.out; }
const total = o + OUTRO;
const outAt = (src) => { const s = segs.find((x) => src >= x.a && src <= x.b) || segs[segs.length - 1]; return s.o + (src - s.a) / s.k; };

// --- cards and overlays, rendered with the TV's font ---------------------------------------------------------------
const font = 'file://' + path.join(root, 'tv/web/fonts/Outfit.woff2');
const css = `@font-face{font-family:Outfit;src:url(${font})}*{margin:0;box-sizing:border-box}body{font-family:Outfit,sans-serif;color:#f4f1ea}
.ember{background:linear-gradient(100deg,#ffc56b,#ff6a3d 50%,#e5306c);-webkit-background-clip:text;background-clip:text;color:transparent}
.pill{display:inline-flex;align-items:center;gap:14px;padding:12px 26px;border-radius:999px;background:rgba(12,10,12,.86);
border:1px solid rgba(255,255,255,.14);font-size:26px;font-weight:500;letter-spacing:.01em}
.dot{width:12px;height:12px;border-radius:50%;background:linear-gradient(135deg,#ffc56b,#e5306c)}`;
const tests = report.steps.map((s) => s.end && s.end.tests).filter(Boolean).pop() || '';
const passed = (tests.match(/(\d+)\/(\d+)/) || []).slice(1);
const seconds = report.steps.reduce((sum, s) => sum + (s.seconds || 0), 0);
const cards = {
  intro: `<body style="width:1920px;height:1080px;background:radial-gradient(ellipse at 30% 20%,#2a1410,#0a090b 60%);display:grid;place-items:center;text-align:center">
    <div><div style="font-size:150px;font-weight:600;letter-spacing:-.02em"><span class="ember">Fire</span>Dev</div>
    <div style="font-size:52px;margin-top:10px;color:#e9e3d8">Build real software by voice, on your TV</div>
    <div style="font-size:30px;margin-top:60px;color:#9b948a">A real rehearsal with real Codex. Waits are fast-forwarded and labelled.</div></div></body>`,
  outro: `<body style="width:1920px;height:1080px;background:radial-gradient(ellipse at 70% 80%,#2a1410,#0a090b 60%);display:grid;place-items:center;text-align:center">
    <div><div style="font-size:130px;font-weight:600;letter-spacing:-.02em"><span class="ember">Fire</span>Dev</div>
    <div style="font-size:46px;margin-top:16px;color:#e9e3d8">${report.steps.length} spoken requests · 0 remote presses${passed.length ? ` · ${passed[0]}/${passed[1]} browser checks passed` : ''}</div>
    <div style="font-size:34px;margin-top:22px;color:#9b948a">${Math.round(seconds / 60)} minutes of real Codex work, from an empty folder to a finished website</div>
    <div style="font-size:30px;margin-top:60px;color:#9b948a">Your machine · your agent · your approval</div></div></body>`,
  tag: `<body style="width:1920px;height:1080px;background:transparent"><div style="position:absolute;left:50%;top:14px;transform:translateX(-50%)">
    <span class="pill" style="font-size:22px;padding:8px 20px"><span class="dot"></span>Real Codex · real files · nothing mocked</span></div></body>`,
};
const ffs = [...new Set(segs.filter((s) => s.k > 1).map((s) => s.k))];
for (const f of ffs) cards[`ff${f}`] = `<body style="width:1920px;height:1080px;background:transparent"><div style="position:absolute;right:40px;top:14px">
  <span class="pill" style="font-size:22px;padding:8px 20px">▶▶ Fast-forward ${f}×</span></div></body>`;
says.forEach((s, i) => {
  const text = s.text.replace(/^“|”$/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
  cards[`say${i}`] = `<body style="width:1920px;height:1080px;background:transparent;display:flex;align-items:flex-end;justify-content:center;padding-bottom:170px">
    <div style="max-width:1500px;padding:26px 40px;border-radius:28px;background:rgba(12,10,12,.9);border:2px solid rgba(255,140,90,.55);box-shadow:0 20px 60px rgba(0,0,0,.6)">
    <div style="font-size:24px;letter-spacing:.14em;font-weight:600" class="ember">YOU SAY</div>
    <div style="font-size:44px;line-height:1.25;margin-top:6px">“${text}”</div></div></body>`;
});
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
for (const [name, html] of Object.entries(cards)) {
  await page.setContent(`<!doctype html><style>${css}</style>${html}`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(work, name + '.png'), omitBackground: !['intro', 'outro'].includes(name) });
}
await browser.close();

// --- ffmpeg --------------------------------------------------------------------------------------------------------
// Each stretch is its own seeked input, so nothing is decoded twice.
for (const s of segs) { s.va = toVideo(s.a); s.vb = toVideo(s.b); }
const inputs = segs.flatMap((s) => ['-ss', s.va.toFixed(3), '-t', (s.vb - s.va).toFixed(3), '-i', video]);
const pngs = Object.keys(cards);
for (const name of pngs) inputs.push('-loop', '1', '-t', String(total), '-i', path.join(work, name + '.png'));
const idx = (name) => segs.length + pngs.indexOf(name);
const f = [];
f.push(`[${idx('intro')}:v]trim=0:${INTRO},setpts=PTS-STARTPTS,fps=30,format=yuv420p[intro]`);
f.push(`[${idx('outro')}:v]trim=0:${OUTRO},setpts=PTS-STARTPTS,fps=30,format=yuv420p[outro]`);
// Each stretch plays at its speed in timeline time, whatever the recording's clock did meanwhile.
segs.forEach((s, i) => f.push(`[${i}:v]setpts=(PTS-STARTPTS)*${(s.out / Math.max(0.001, s.vb - s.va)).toFixed(6)},fps=30,format=yuv420p,trim=0:${s.out.toFixed(3)}[s${i}]`));
f.push(`[intro]${segs.map((_, i) => `[s${i}]`).join('')}[outro]concat=n=${segs.length + 2}:v=1:a=0[cut]`);
let last = 'cut', n = 0;
const over = (name, ranges) => {
  if (!ranges.length) return;
  const en = ranges.map(([a, b]) => `between(t,${a.toFixed(2)},${b.toFixed(2)})`).join('+');
  f.push(`[${last}][${idx(name)}:v]overlay=0:0:enable='${en}'[o${n}]`);
  last = `o${n++}`;
};
over('tag', [[INTRO + 3, total - OUTRO]]);
for (const ff of ffs) over(`ff${ff}`, segs.filter((s) => s.k === ff && s.out > 0.4).map((s) => [s.o, s.o + s.out]));
says.forEach((s, i) => { const a = outAt(s.t); over(`say${i}`, [[a - 0.3, a + 5.5]]); });
f.push(`[${last}]fade=t=in:st=0:d=0.6,fade=t=out:st=${(total - 0.8).toFixed(2)}:d=0.8[final]`);
fs.writeFileSync(path.join(work, 'filter.txt'), f.join(';\n'));
const args = ['-y', '-hide_banner', '-loglevel', 'error', ...inputs, '-filter_complex_script', path.join(work, 'filter.txt'), '-map', '[final]',
  '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-t', total.toFixed(2), outFile];
const r = spawnSync('ffmpeg', args, { stdio: 'inherit' });
if (r.status !== 0) process.exit(r.status || 1);
fs.writeFileSync(path.join(work, 'segments.json'), JSON.stringify({ length, total, segs, says }, null, 2));
console.log(`${outFile}: ${total.toFixed(1)} s from ${videoLength.toFixed(0)} s of recording, ${segs.filter((s) => s.k === 1).length} real-time stretches, fast-forward up to ${Math.max(...segs.map((s) => s.k))}×`);
