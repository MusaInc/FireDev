// Browser checks for the Aurora Lodge site: loads it in headless Chromium at phone, tablet, laptop and TV sizes.
// Run with ./test.sh (or `firedev run test`). Prints one line per check and a summary FireDev reads.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire((process.env.FIREDEV_ROOT || process.env.HOME + '/workspace/projects/FireDev') + '/');
const { chromium } = require('playwright');

const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2', '.ico': 'image/x-icon', '.json': 'application/json' };
const missing = [];
const server = http.createServer((req, res) => {
  const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/\/$/, '/index.html');
  const file = path.join(root, path.normalize(rel));
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    if (!/favicon\.ico$/.test(rel)) missing.push(rel);
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}/`;

const results = [];
const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail: ok ? '' : detail || '' });

if (!fs.existsSync(path.join(root, 'index.html'))) {
  check('index.html exists', false, 'there is no index.html yet');
} else {
  const browser = await chromium.launch();
  const errors = [];
  const remote = [];
  const open = async (width, height, extra = {}) => {
    const ctx = await browser.newContext({ viewport: { width, height }, ...extra });
    // No network for the checks: remote fonts fall back; remote images are reported.
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => {
      const t = route.request().resourceType();
      if (t !== 'font' && t !== 'stylesheet') remote.push(route.request().url());
      route.abort();
    });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`${width}px: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error' && !/net::ERR_FAILED|Failed to load resource/.test(m.text())) errors.push(`${width}px: ${m.text()}`); });
    await page.goto(base, { waitUntil: 'load' });
    await page.waitForTimeout(600);
    return { ctx, page };
  };

  const { ctx, page } = await open(1440, 900);
  const doc = await page.evaluate(() => {
    const ids = new Set([...document.querySelectorAll('[id]')].map((e) => e.id));
    const anchors = [...document.querySelectorAll('a[href^="#"]')].map((a) => a.getAttribute('href')).filter((h) => h.length > 1);
    let reduced = false;
    const walk = (rules) => { for (const r of rules) { if (r.media && /prefers-reduced-motion/.test(r.media.mediaText)) reduced = true; if (r.cssRules) try { walk(r.cssRules); } catch {} } };
    for (const s of document.styleSheets) { try { walk(s.cssRules); } catch {} }
    return {
      lang: document.documentElement.lang, title: document.title.trim(),
      viewport: !!document.querySelector('meta[name=viewport][content*="width=device-width"]'),
      description: (document.querySelector('meta[name=description]') || {}).content || '',
      h1: document.querySelectorAll('h1').length,
      landmarks: ['header', 'main', 'footer'].filter((t) => !document.querySelector(t)),
      noAlt: [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).map((i) => i.getAttribute('src')),
      broken: [...document.querySelectorAll('img')].filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.getAttribute('src')),
      deadLinks: [...new Set(anchors.filter((h) => !ids.has(decodeURIComponent(h.slice(1)))))],
      sections: document.querySelectorAll('main section, main > article').length,
      words: (document.body.innerText || '').split(/\s+/).filter(Boolean).length,
      lorem: /lorem ipsum/i.test(document.body.innerText || ''),
      reduced,
    };
  });
  check('Page has a language, title, description and mobile viewport', doc.lang && doc.title && doc.description && doc.viewport,
    `lang="${doc.lang}" title="${doc.title}" description=${!!doc.description} viewport=${doc.viewport}`);
  check('Exactly one main heading (h1)', doc.h1 === 1, `found ${doc.h1}`);
  check('Header, main and footer landmarks', doc.landmarks.length === 0, `missing ${doc.landmarks.join(', ')}`);
  check('At least four content sections with real copy', doc.sections >= 4 && doc.words >= 250 && !doc.lorem,
    `${doc.sections} sections, ${doc.words} words${doc.lorem ? ', lorem ipsum' : ''}`);
  check('Every image has alt text', doc.noAlt.length === 0, `no alt: ${doc.noAlt.join(', ')}`);
  check('Every in-page link has a target', doc.deadLinks.length === 0, `no target for ${doc.deadLinks.join(', ')}`);
  check('Respects reduced-motion preferences', doc.reduced, 'no @media (prefers-reduced-motion) rule');
  await ctx.close();

  for (const [w, h, label] of [[390, 844, 'phone'], [768, 1024, 'tablet'], [1440, 900, 'laptop'], [1920, 1080, 'TV']]) {
    const v = await open(w, h, w < 800 ? { isMobile: true, hasTouch: true } : {});
    const over = await v.page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const wide = [...document.querySelectorAll('body *')].filter((e) => {
        const r = e.getBoundingClientRect();
        if (r.width === 0 || r.right <= vw + 1) return false;
        for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'hidden' || o === 'clip' || o === 'auto' || o === 'scroll') return false; }
        return true;
      }).slice(0, 3).map((e) => e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/)[0] : ''));
      return { scroll: document.documentElement.scrollWidth - vw, wide };
    });
    check(`No sideways scrolling on a ${label} (${w}px)`, over.scroll <= 1, `page is ${over.scroll}px too wide${over.wide.length ? ' (' + over.wide.join(', ') + ')' : ''}`);
    await v.ctx.close();
  }
  check('No script errors', errors.length === 0, [...new Set(errors)].slice(0, 3).join(' | '));
  check('Every local file it uses exists', missing.length === 0 && doc.broken.length === 0, [...new Set([...missing, ...doc.broken])].join(', '));
  check('No remote images or scripts (it must work offline)', remote.length === 0, [...new Set(remote)].slice(0, 3).join(', '));
  await browser.close();
}
server.close();

for (const r of results) console.log(`${r.ok ? '  ✓' : '  ✗'} ${r.name}${r.ok ? '' : ' — ' + r.detail}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`\nTests:       ${failed ? failed + ' failed, ' : ''}${results.length - failed} passed, ${results.length} total`);
process.exit(failed ? 1 : 0);
