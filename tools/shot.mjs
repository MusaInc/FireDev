import { chromium } from '@playwright/test';
const [,, url, out, ...keys] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
const errs = [];
p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
p.on('pageerror', (e) => errs.push(String(e)));
await p.goto(url);
await p.waitForTimeout(1500);
for (const k of keys) {
  if (k.startsWith('wait:')) { await p.waitForTimeout(Number(k.slice(5))); continue; }
  if (k.startsWith('shot:')) { await p.screenshot({ path: k.slice(5) }); continue; }
  await p.keyboard.press(k); await p.waitForTimeout(350);
}
await p.screenshot({ path: out });
console.log(errs.length ? 'ERRORS:\n' + errs.join('\n') : 'no console errors');
await b.close();
