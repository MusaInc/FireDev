// Bundles tv/web/src into tv/web/dist/app.js for the Fire TV WebView (old Chromium too) and the browser preview.
import * as esbuild from 'esbuild';

const watch = process.argv.includes('--watch');
const opts = {
  entryPoints: ['tv/web/src/main.js'],
  bundle: true,
  format: 'iife',
  target: ['chrome64'],
  outfile: 'tv/web/dist/app.js',
  sourcemap: watch ? 'inline' : false,
  minify: !watch,
  logLevel: 'info',
};
if (watch) {
  const ctx = await esbuild.context(opts);
  await ctx.watch();
} else {
  await esbuild.build(opts);
}
