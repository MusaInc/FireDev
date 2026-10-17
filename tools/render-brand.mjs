// Renders FireDev's launcher icons, Fire TV banner and a feature graphic from tools/brand/brand.html.
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const res = path.join(root, 'tv/android/app/src/main/res');
const out = path.join(root, 'docs/brand');
fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch();
async function render(kind, w, h, file) {
  const p = await b.newPage({ viewport: { width: w, height: h } });
  await p.goto('file://' + path.join(root, 'tools/brand/brand.html') + '?kind=' + kind);
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(200);
  await p.screenshot({ path: file, clip: { x: 0, y: 0, width: w, height: h } });
  await p.close();
}
await render('icon', 512, 512, path.join(out, 'icon-512.png'));
await render('banner', 1280, 720, path.join(out, 'banner-1280x720.png'));
await render('feature', 1920, 1080, path.join(out, 'feature-1920x1080.png'));
await b.close();
const sizes = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144 };
for (const [d, s] of Object.entries(sizes)) {
  fs.mkdirSync(path.join(res, 'mipmap-' + d), { recursive: true });
  execFileSync('magick', [path.join(out, 'icon-512.png'), '-resize', `${s}x${s}`, path.join(res, `mipmap-${d}/ic_launcher.png`)]);
}
execFileSync('magick', [path.join(out, 'banner-1280x720.png'), '-resize', '320x180', path.join(res, 'drawable/tv_banner.png')]);
console.log('brand rendered');

// The architecture diagram, rendered with the real fonts.
{
  const svgText = fs.readFileSync(path.join(root, 'docs/architecture.svg'), 'utf8');
  const html = `<style>@font-face{font-family:Outfit;src:url("file://${root}/tv/web/fonts/Outfit.woff2")}
    @font-face{font-family:"Noto Sans Mono";src:url("file://${root}/tv/web/fonts/NotoSansMono-Regular.woff2")}
    body{margin:0;background:#09090b} svg{display:block;width:1600px;height:900px}</style>${svgText}`;
  const br = await chromium.launch();
  const p = await br.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.5 });
  const tmp = path.join(root, 'tools/brand/.architecture.html');
  fs.writeFileSync(tmp, html);
  await p.goto('file://' + tmp);
  await p.evaluate(() => document.fonts.ready);
  await p.screenshot({ path: path.join(root, 'docs/architecture.png') });
  await br.close();
  fs.unlinkSync(tmp);
  console.log('architecture rendered');
}
