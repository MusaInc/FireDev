/**
 * Camera direction for the live preview: which part of the page a real code change touched, as hints the page's
 * helper can look up (preview_tv.js, op "focus"). Pure functions of a code_change event: only the agent's real
 * edited lines and their context are read, and when nothing maps reliably the answer is "no hints" (the view stays).
 *
 *   HTML   ids and classes on the changed lines, else on the nearest opening tag above them; landmark tags
 *   CSS    the selectors of the rules the changed lines belong to (not :root, *, html or body: those are everywhere)
 *   SVG and images   the elements that use that file
 */

const GLOBAL = /^(:root|\*|html|body|::?[\w-]+|@[\w-]+)$/i;
const LANDMARK = /<(header|nav|footer|main|section|article|aside|form)\b/i;
const MAX_HINTS = 6;

function base(p) { return String(p || '').split('/').pop(); }

/** Hints for one code_change event's data: [{ sel } | { asset }], most specific first. */
export function hintsFor(change) {
  if (!change || change.change === 'delete') return [];
  const lang = change.lang;
  const regions = change.regions || [];
  const out = [];
  const add = (h) => { if (h && !out.some((o) => JSON.stringify(o) === JSON.stringify(h)) && out.length < MAX_HINTS) out.push(h); };
  const name = base(change.path);
  if (/\.(svg|png|jpe?g|webp|gif|avif)$/i.test(name)) {
    add({ asset: name });
    return out;
  }
  for (const r of regions) {
    const changed = (r.added || []).concat(r.removed || []);
    if (lang === 'html') htmlHints(changed, r.before || []).forEach(add);
    else if (lang === 'css') cssHints(r).forEach(add);
    else if (lang === 'js') jsHints(changed).forEach(add);
  }
  return out;
}

function attrHints(line) {
  const out = [];
  const id = line.match(/\bid\s*=\s*["']([\w-]+)["']/i);
  if (id) out.push({ sel: '#' + id[1] });
  const cls = line.match(/\bclass\s*=\s*["']([^"']+)["']/i);
  if (cls) {
    const first = cls[1].trim().split(/\s+/).find((c) => /^[A-Za-z_][\w-]*$/.test(c));
    if (first) out.push({ sel: '.' + first });
  }
  const tag = line.match(LANDMARK);
  if (tag && !id && !cls) out.push({ sel: tag[1].toLowerCase() });
  return out;
}

function htmlHints(changed, before) {
  const out = [];
  changed.forEach((l) => attrHints(l).forEach((h) => out.push(h)));
  if (out.length) return out;
  // Text-only edits: the nearest opening tag above them with an id, a class or a landmark.
  for (let i = before.length - 1; i >= 0; i--) {
    if (!/<[a-z]/i.test(before[i])) continue;
    const h = attrHints(before[i]);
    if (h.length) return h;
  }
  return out;
}

/** The selector of each rule the changed lines are in: a changed `.hero h1 {` line, or the nearest `{` above a
 *  changed declaration. Nested at-rules (@media) are looked through. */
function cssHints(r) {
  const out = [];
  const sels = (text) => text.split(',').map((s) => s.replace(/::?[\w-]+(\([^)]*\))?/g, '').trim()).filter((s) => s && !GLOBAL.test(s) && !/^(from|to|\d+%)$/.test(s));
  const ruleOf = (line) => { const m = String(line).match(/^\s*([^{}@][^{}]*?)\s*\{/); return m ? m[1] : null; };
  const changed = (r.added || []).concat(r.removed || []);
  let found = false;
  changed.forEach((l) => { const rule = ruleOf(l); if (rule) { found = true; sels(rule).forEach((s) => out.push({ sel: s })); } });
  if (!found) {
    const before = r.before || [];
    for (let i = before.length - 1; i >= 0; i--) {
      if (/^\s*\}/.test(before[i])) break; // the changed lines are between rules
      const rule = ruleOf(before[i]);
      if (rule) { sels(rule).forEach((s) => out.push({ sel: s })); break; }
    }
  }
  return out;
}

function jsHints(changed) {
  const out = [];
  changed.forEach((l) => {
    const re = /querySelector(?:All)?\(\s*['"]([^'"]{2,60})['"]\s*\)|getElementById\(\s*['"]([\w-]+)['"]\s*\)/g;
    let m;
    while ((m = re.exec(l))) out.push({ sel: m[1] || '#' + m[2] });
  });
  return out;
}
