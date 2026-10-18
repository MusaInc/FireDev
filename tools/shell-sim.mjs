// Loads the TV interface the way the Fire TV shell does: from local files, with a stand-in FireDevNative bridge,
// at the WebView's CSS viewport (mobile viewport rules on, as in Android WebView). Reports page errors, failed loads, unloaded fonts and the screen it reaches.
//   node tools/shell-sim.mjs [server] [screenshot.png]
import { chromium } from '@playwright/test';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const server = process.argv[2] || 'http://127.0.0.1:8130';
const shot = process.argv[3];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, isMobile: true });
const problems = [];
page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') problems.push('console: ' + m.text()); });
page.on('requestfailed', (r) => problems.push('requestfailed: ' + r.url() + ' ' + (r.failure() && r.failure().errorText)));
await page.addInitScript((srv) => {
  window.FireDevNative = {
    getConfig: () => JSON.stringify({ server: srv, token: null, version: 'shell-sim' }),
    speechAvailable: () => false, startSpeech() {}, stopSpeech() {}, attention() {}, exitApp() {},
  };
}, server);
await page.goto(pathToFileURL(path.resolve('tv/web/index.html')).href);
await page.waitForFunction(() => window.__firedev && window.__firedev.status !== 'connecting', null, { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(800);
const state = await page.evaluate(() => {
  const stage = document.getElementById('stage');
  const screen = document.getElementById('screen');
  const r = screen.getBoundingClientRect();
  return {
    app: !!window.__firedev, status: window.__firedev && window.__firedev.status, route: window.__firedev && window.__firedev.route.name,
    transform: stage.style.transform, screenBox: [Math.round(r.width), Math.round(r.height)],
    text: screen.innerText.replace(/\s+/g, ' ').slice(0, 160),
    fonts: [...document.fonts].map((f) => f.family + ':' + f.status),
  };
});
if (shot) await page.screenshot({ path: shot });
console.log(JSON.stringify({ state, problems }, null, 1));
await browser.close();
