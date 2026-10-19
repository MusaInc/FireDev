// Camera direction for the live preview: the TV's mapping from a real edit to page elements (camera.js), and the
// page helper's tour, follow and highlight (preview_tv.js) running in a frame like on the TV.
import { test, expect } from '@playwright/test';
import http from 'node:http';
import fs from 'node:fs';

// camera.js is a browser module (the package isn't "type: module"): load it as one.
const { hintsFor } = await import('data:text/javascript,' + encodeURIComponent(fs.readFileSync(new URL('../tv/web/src/camera.js', import.meta.url), 'utf8')));

const HELPER = fs.readFileSync(new URL('../backend/firedev/preview_tv.js', import.meta.url), 'utf8');
const section = (id, cls, h, n) => `<section id="${id}" class="${cls}" style="min-height:${n}px;padding:40px"><h2>${h}</h2><p>Real copy.</p></section>`;
const PAGE = `<!doctype html><html><head><title>Lodge</title><style>body{margin:0;font:20px sans-serif} .card{height:40px}</style></head><body>
<a class="skip-link" href="#top" style="position:absolute;left:12px;top:-120px;padding:12px">Skip to content</a>
<header class="site-header" style="height:120px">Aurora</header>
<main>${section('top', 'hero', 'Stay close to wonder', 700).replace('<h2>', '<h2 id="hero-title">')}${section('rooms', 'rooms', 'The cabins', 800)}
<div class="cards">${Array.from({ length: 8 }, () => '<div class="card">card</div>').join('')}</div>
${section('booking', 'booking', 'Plan your stay', 700).replace('<p>', '<form class="booking-form"><input name="d"></form><p>')}
${section('story', 'story', 'Lapland', 900).replace('<p>', '<img class="sky" src="/assets/aurora-sky.svg" width="400" height="200" alt="sky"><p>')}</main>
<footer style="height:300px">Footer</footer><script>${HELPER}</script></body></html>`;
const PARENT = `<!doctype html><body style="margin:0"><iframe id="f" src="/page.html" style="width:1200px;height:600px;border:0"></iframe>
<script>window.__msgs=[];addEventListener('message',function(e){window.__msgs.push(e.data)});
window.send=function(m){m.fd='cmd';document.getElementById('f').contentWindow.postMessage(m,'*')};</script></body>`;
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="#123"/></svg>';

let server, base;
test.beforeAll(async () => {
  server = http.createServer((req, res) => {
    const body = req.url === '/page.html' ? PAGE : req.url.startsWith('/assets/') ? SVG : PARENT;
    res.writeHead(200, { 'content-type': req.url.startsWith('/assets/') ? 'image/svg+xml' : 'text/html' });
    res.end(body);
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(() => server.close());

async function open(page) {
  await page.goto(base + '/');
  await expect.poll(() => page.evaluate(() => window.__msgs.some((m) => m.fd === 'hello'))).toBe(true);
  return page.frameLocator('#f');
}
const scrollY = (page) => page.evaluate(() => document.getElementById('f').contentWindow.scrollY);
const camMsgs = (page) => page.evaluate(() => window.__msgs.filter((m) => m.fd === 'camera'));
const send = (page, m) => page.evaluate((x) => window.send(x), m);

test('an edit maps to the elements it touched, and nothing when it says nothing', () => {
  const html = { path: 'index.html', lang: 'html', regions: [{ before: ['<section id="booking" class="booking">', '  <h2>Plan</h2>'], added: ['  <p>New copy for the stay.</p>'], removed: [] }] };
  expect(hintsFor(html)).toEqual([{ sel: '#booking' }, { sel: '.booking' }]);
  const tagged = { path: 'index.html', lang: 'html', regions: [{ before: [], added: ['<form class="booking-form" id="enquire">'], removed: [] }] };
  expect(hintsFor(tagged)[0]).toEqual({ sel: '#enquire' });
  const css = { path: 'styles.css', lang: 'css', regions: [{ before: ['.hero h1,', '.hero .lede {'], added: ['  font-size: 120px;'], removed: ['  font-size: 80px;'] }] };
  expect(hintsFor(css)).toEqual([{ sel: '.hero .lede' }]); // the rule's opening line names it
  const rule = { path: 'styles.css', lang: 'css', regions: [{ before: [], added: ['.rooms .card:hover { transform: translateY(-4px); }'], removed: [] }] };
  expect(hintsFor(rule)).toEqual([{ sel: '.rooms .card' }]);
  const theme = { path: 'styles.css', lang: 'css', regions: [{ before: [':root {'], added: ['  --ink: #0b1020;'], removed: [] }] };
  expect(hintsFor(theme)).toEqual([]);
  expect(hintsFor({ path: 'assets/aurora-sky.svg', lang: 'html', regions: [] })).toEqual([{ asset: 'aurora-sky.svg' }]);
  expect(hintsFor({ path: 'script.js', lang: 'js', regions: [{ before: [], added: ['const menu = document.querySelector(".site-nav");'], removed: [] }] })).toEqual([{ sel: '.site-nav' }]);
  expect(hintsFor({ path: 'index.html', lang: 'html', change: 'delete', regions: [] })).toEqual([]);
});

test('a rule edited inside a multi-line selector list maps to that rule', () => {
  const css = { path: 'styles.css', lang: 'css', regions: [{ before: ['.hero h1 {'], added: ['  letter-spacing: -0.03em;'], removed: [] }] };
  expect(hintsFor(css)).toEqual([{ sel: '.hero h1' }]);
});

test('while nothing changes, the camera tours the real sections gently, then the viewer takes over', async ({ page }) => {
  await open(page);
  expect(await scrollY(page)).toBe(0);
  await send(page, { op: 'camera', mode: 'tour' });
  // A moment on the current view, then an eased move (not a jump) to the next real section.
  await page.waitForTimeout(2200);
  expect(await scrollY(page)).toBe(0);
  const samples = [];
  for (let i = 0; i < 12; i++) { samples.push(await scrollY(page)); await page.waitForTimeout(220); }
  const moved = samples.filter((y, i) => i && y !== samples[i - 1]).length;
  expect(moved).toBeGreaterThan(4); // many small steps
  // It stops with the next real section (the hero, under the header) at the top of the view.
  const hero = await page.frameLocator('#f').locator('#top').boundingBox();
  expect(Math.abs(hero.y - 600 * 0.06)).toBeLessThan(6);
  expect((await camMsgs(page)).some((m) => m.state === 'tour')).toBe(true);
  // It holds there (no constant movement).
  const held = await scrollY(page);
  await page.waitForTimeout(2500);
  expect(await scrollY(page)).toBe(held);
  // The viewer scrolls: the tour stops at once and says so.
  await page.frameLocator('#f').locator('body').hover();
  await page.mouse.wheel(0, 200);
  await expect.poll(async () => (await camMsgs(page)).some((m) => m.state === 'paused')).toBe(true);
  const after = await scrollY(page);
  await page.waitForTimeout(9000);
  expect(Math.abs((await scrollY(page)) - after)).toBeLessThan(5);
});

test('an edit brings its section into view and highlights it once rendered; vague edits keep the view', async ({ page }) => {
  const f = await open(page);
  await send(page, { op: 'focus', hints: [{ sel: '.booking-form' }], highlight: true });
  await expect.poll(async () => (await camMsgs(page)).find((m) => m.state === 'follow')).toMatchObject({ name: 'booking' });
  await page.waitForTimeout(1800);
  const box = await f.locator('#booking').boundingBox();
  expect(box.y).toBeGreaterThan(-5);
  expect(box.y).toBeLessThan(600);
  const hl = await page.evaluate(() => { const d = document.getElementById('f').contentDocument; const el = [...d.documentElement.children].find((x) => x.style && x.style.borderRadius === '14px'); return el ? el.style.opacity : null; });
  expect(hl).toBe('1');
  // ".card" matches eight things: it says nothing about where to look.
  const y = await scrollY(page);
  await send(page, { op: 'focus', hints: [{ sel: '.card' }], highlight: true });
  await expect.poll(async () => (await camMsgs(page)).some((m) => m.state === 'kept')).toBe(true);
  await page.waitForTimeout(1600);
  expect(await scrollY(page)).toBe(y);
  // An image file: the element that shows it.
  await send(page, { op: 'focus', hints: [{ asset: 'aurora-sky.svg' }] });
  await page.waitForTimeout(1900);
  const img = await f.locator('img.sky').boundingBox();
  expect(img.y).toBeGreaterThan(-5);
  expect(img.y + img.height).toBeLessThan(605);
});

test('edits are named by their section, and parked elements are ignored', async ({ page }) => {
  const f = await open(page);
  await page.evaluate(() => document.getElementById('f').contentWindow.scrollTo(0, 2000));
  // The skip link matches but sits off the page: the headline (with its own id) is the real target, named by its section.
  await send(page, { op: 'focus', hints: [{ sel: '.skip-link' }, { sel: '#hero-title' }], highlight: true });
  await expect.poll(async () => (await camMsgs(page)).find((m) => m.state === 'follow')).toMatchObject({ name: 'hero' });
  await page.waitForTimeout(1800);
  const h = await f.locator('#hero-title').boundingBox();
  expect(h.y).toBeGreaterThan(0);
  expect(h.y).toBeLessThan(600);
});
