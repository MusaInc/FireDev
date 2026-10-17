/**
 * D-pad focus engine: a stack of layers (the screen, then overlays on top). Only the top layer is reachable.
 * Arrows move spatially to the nearest control in that direction; a control can claim an arrow by handling it
 * (returning true). Screens re-render often, so focus is remembered by data-focus-id and restored after a
 * re-render replaces the element.
 */

import { handlersOf } from './util.js';

export function createFocus(opts) {
  const onMove = (opts && opts.onMove) || function () {};
  const layers = [];
  const top = () => layers[layers.length - 1] || null;
  const visible = (el) => el.offsetParent !== null && el.getAttribute('aria-disabled') !== 'true';
  const items = (layer) => Array.prototype.filter.call(layer.root.querySelectorAll('.focusable'), visible);

  function apply(layer, el) {
    if (layer.focused && layer.focused !== el) layer.focused.classList.remove('is-focused');
    layer.focused = el;
    layer.focusId = el ? el.dataset.focusId || null : null;
    if (el) {
      el.classList.add('is-focused');
      if (layer.onFocus) layer.onFocus(el);
    }
  }

  function center(r) {
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function nearest(from, dir, candidates) {
    const a = from.getBoundingClientRect();
    const ca = center(a);
    let best = null;
    let bestScore = Infinity;
    for (const el of candidates) {
      if (el === from) continue;
      const b = el.getBoundingClientRect();
      const cb = center(b);
      let primary;
      let cross;
      if (dir === 'right') { primary = b.left - a.right; cross = Math.abs(cb.y - ca.y); if (cb.x <= ca.x + 1) continue; }
      else if (dir === 'left') { primary = a.left - b.right; cross = Math.abs(cb.y - ca.y); if (cb.x >= ca.x - 1) continue; }
      else if (dir === 'down') { primary = b.top - a.bottom; cross = Math.abs(cb.x - ca.x); if (cb.y <= ca.y + 1) continue; }
      else { primary = a.top - b.bottom; cross = Math.abs(cb.x - ca.x); if (cb.y >= ca.y - 1) continue; }
      const overlap = dir === 'left' || dir === 'right'
        ? Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
        : Math.min(a.right, b.right) - Math.max(a.left, b.left);
      if (overlap > 0) cross *= 0.3;
      const score = Math.max(0, primary) + cross * 2.2;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    return best;
  }

  const api = {
    push(root, o) {
      o = o || {};
      const t = top();
      if (t && t.focused) t.focused.classList.remove('is-focused');
      const layer = { root, id: o.id, focused: null, focusId: null, onBack: o.onBack, onFocus: o.onFocus, onKey: o.onKey };
      layers.push(layer);
      api.focus(o.initial || null);
      return layer;
    },
    pop(layer) {
      layer = layer || top();
      const i = layers.indexOf(layer);
      if (i < 0) return;
      if (layer.focused) layer.focused.classList.remove('is-focused');
      layers.splice(i, 1);
      api.restore();
    },
    remove(id) {
      for (const l of layers.slice()) if (l.id === id) api.pop(l);
    },
    reset(root, o) {
      while (layers.length) {
        const l = layers.pop();
        if (l.focused) l.focused.classList.remove('is-focused');
      }
      return api.push(root, o);
    },
    has(id) { return layers.some((l) => l.id === id); },
    focus(target) {
      const layer = top();
      if (!layer) return;
      let el = null;
      if (typeof target === 'string') el = layer.root.querySelector('[data-focus-id="' + target + '"]');
      else if (target) el = target;
      if (el && !visible(el)) el = null;
      if (!el) el = items(layer)[0] || null;
      apply(layer, el);
    },
    /** After a re-render: put focus back on the element with the same focus id, or the first control. */
    restore() {
      const layer = top();
      if (!layer) return;
      if (layer.focused && layer.root.contains(layer.focused) && visible(layer.focused)) {
        layer.focused.classList.add('is-focused');
        return;
      }
      const again = layer.focusId ? layer.root.querySelector('[data-focus-id="' + layer.focusId + '"]') : null;
      apply(layer, again && visible(again) ? again : items(layer)[0] || null);
    },
    move(dir) {
      const layer = top();
      if (!layer) return false;
      if (!layer.focused || !layer.root.contains(layer.focused)) api.restore();
      const el = layer.focused;
      if (!el) return false;
      const own = handlersOf(el)[dir];
      if (own && own() === true) { onMove(); return true; }
      const next = nearest(el, dir, items(layer));
      if (next) { apply(layer, next); onMove(); return true; }
      return false;
    },
    select() {
      const t = top();
      const el = t && t.focused;
      const fn = el && handlersOf(el).select;
      if (fn) { fn(el); return true; }
      return false;
    },
    back() {
      const layer = top();
      if (layer && layer.onBack) { layer.onBack(); return true; }
      return false;
    },
    key(action, down) {
      const layer = top();
      if (layer && layer.onKey) return layer.onKey(action, down) === true;
      return false;
    },
    get top() { return top(); },
    get depth() { return layers.length; },
    focusedId() { const t = top(); return t && t.focused ? t.focused.dataset.focusId || null : null; },
    focusedEl() { const t = top(); return t ? t.focused : null; },
  };
  return api;
}
