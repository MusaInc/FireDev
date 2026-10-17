/** Small DOM and formatting helpers. */

export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = (el.className ? el.className + ' ' : '') + v;
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') el[k.toLowerCase()] = v;
      else if (k === 'focus') el.dataset.focusId = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function svg(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}

/** Makes an element focusable for the D-pad engine. handlers: { select, left, right, up, down } */
export function focusable(el, id, handlers) {
  el.classList.add('focusable');
  if (id) el.dataset.focusId = id;
  el.__fd = handlers || {};
  return el;
}

export function handlersOf(el) {
  return (el && el.__fd) || {};
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  return m + 'm ' + String(s % 60).padStart(2, '0') + 's';
}

export function clock(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

export function bytes(n) {
  if (!n && n !== 0) return '';
  if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

export function plural(n, word, many) {
  return n + ' ' + (n === 1 ? word : (many || word + 's'));
}

/** Splits an agent's final message into a headline and up to three bullet points for the TV. */
export function summarize(text) {
  if (!text) return { headline: '', bullets: [] };
  const clean = text.replace(/```[\s\S]*?```/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[`*_]/g, '').trim();
  const lines = clean.split('\n').map((l) => l.trim()).filter(Boolean);
  const bullets = [];
  let headline = '';
  for (const l of lines) {
    const m = l.match(/^(?:[-•]|\d+[.)])\s+(.*)$/);
    if (m) bullets.push(m[1]);
    else if (!headline) headline = l;
    else if (bullets.length === 0 && headline.length < 90) headline += ' ' + l;
    else bullets.push(l);
  }
  if (!headline && bullets.length) headline = bullets.shift();
  const shortHead = headline.length > 150 ? headline.slice(0, 147).replace(/\s+\S*$/, '') + '…' : headline;
  return { headline: shortHead, bullets: bullets.slice(0, 3).map((b) => (b.length > 120 ? b.slice(0, 117).replace(/\s+\S*$/, '') + '…' : b)) };
}

export const icons = {
  mark: '<svg class="mark" viewBox="0 0 52 52" fill="none"><rect x="1.5" y="1.5" width="49" height="49" rx="13" stroke="currentColor" stroke-width="3" opacity=".9"/><path d="M15 33l8-7-8-7" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M27 35h10" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
  check: '<svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="17" stroke="currentColor" stroke-width="3" opacity=".35"/><path d="M12.5 20.5l5 5 10-11" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cross: '<svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="17" stroke="currentColor" stroke-width="3" opacity=".35"/><path d="M14 14l12 12M26 14L14 26" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
  bigcheck: '<svg viewBox="0 0 64 64" fill="none"><path d="M16 33l11 11 22-24" stroke="currentColor" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bigcross: '<svg viewBox="0 0 64 64" fill="none"><path d="M20 20l24 24M44 20L20 44" stroke="currentColor" stroke-width="6" stroke-linecap="round"/></svg>',
  dot: '<svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="6" fill="currentColor" opacity=".55"/></svg>',
  mic: '<svg viewBox="0 0 56 56" fill="none"><rect x="20" y="6" width="16" height="28" rx="8" stroke="currentColor" stroke-width="4"/><path d="M12 26c0 9 7 16 16 16s16-7 16-16M28 42v8" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>',
  file: '<svg viewBox="0 0 40 40" fill="none"><path d="M11 5h13l8 8v22H11z" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/><path d="M24 5v8h8" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/></svg>',
  terminal: '<svg viewBox="0 0 40 40" fill="none"><path d="M9 13l7 7-7 7M20 28h11" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  agent: '<svg viewBox="0 0 40 40" fill="none"><path d="M20 5l3.6 8.4L32 17l-8.4 3.6L20 29l-3.6-8.4L8 17l8.4-3.6z" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/><path d="M31 27l1.5 3.5L36 32l-3.5 1.5L31 37l-1.5-3.5L26 32l3.5-1.5z" fill="currentColor"/></svg>',
  shield: '<svg viewBox="0 0 40 40" fill="none"><path d="M20 4l13 5v10c0 8-5.5 14-13 17-7.5-3-13-9-13-17V9z" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/><path d="M20 13v9M20 27v.5" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
  android: '<svg viewBox="0 0 48 48" fill="none"><path d="M10 30c0-8 6.3-14 14-14s14 6 14 14v4H10z" stroke="currentColor" stroke-width="3.5" stroke-linejoin="round"/><path d="M15 13l3 5M33 13l-3 5" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/><circle cx="19" cy="26" r="2" fill="currentColor"/><circle cx="29" cy="26" r="2" fill="currentColor"/></svg>',
  code: '<svg viewBox="0 0 48 48" fill="none"><path d="M17 14L7 24l10 10M31 14l10 10-10 10" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 72 72" fill="none"><rect x="3" y="3" width="66" height="66" rx="18" stroke="currentColor" stroke-width="3" stroke-dasharray="8 8"/><path d="M36 24v24M24 36h24" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>',
  undo: '<svg viewBox="0 0 40 40" fill="none"><path d="M13 12L6 19l7 7" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M7 19h17a9 9 0 010 18h-6" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
  deploy: '<svg viewBox="0 0 40 40" fill="none"><path d="M20 30V8M12 16l8-8 8 8" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 32h24" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
  wait: '<svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="15" stroke="currentColor" stroke-width="3"/><path d="M20 12v9l6 4" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg>',
};

export function icon(name, cls) {
  const el = svg(icons[name]);
  if (cls) el.setAttribute('class', (el.getAttribute('class') || '') + ' ' + cls);
  return el;
}
