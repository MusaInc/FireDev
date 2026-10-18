/**
 * Live data bindings for generated screens. A value like {"$bind": "project.tests.passed"} is looked up in the
 * TV's current state each time it changes, so a screen stays connected to its data without the agent re-sending
 * it. Unknown paths resolve to the binding's fallback (or "—"): a screen never shows invented values.
 */

import { bytes, clock, duration } from '../util.js';

export const UNAVAILABLE = '—';

export function isBind(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v) && typeof v.$bind === 'string';
}

function walkPath(root, path) {
  const parts = path.match(/[A-Za-z0-9_-]+|\[\d+\]/g) || [];
  let cur = root;
  for (const p of parts) {
    if (cur === null || cur === undefined) return undefined;
    if (p[0] === '[') cur = Array.isArray(cur) ? cur[Number(p.slice(1, -1))] : undefined;
    else cur = cur[p];
  }
  return cur;
}

function pick(obj, field) {
  return field.split('.').reduce((o, k) => (o === null || o === undefined ? undefined : o[k]), obj);
}

/** The data a screen can bind to, built from the TV's live state for one project. */
export function bindingRoot(app, pid) {
  const p = app.project(pid) || {};
  const ui = app.ui || {};
  const history = (ui.data && ui.data.history && ui.data.history[pid]) || { tests: [], builds: [] };
  return {
    project: p,
    projects: (app.home && app.home.projects) || [],
    approvals: app.approvals || [],
    agents: (ui.data && ui.data.agents) || [],
    history,
    conversation: p.conversation || [],
  };
}

export function format(v, fmt) {
  if (v === null || v === undefined || v === '') return UNAVAILABLE;
  if (Array.isArray(v)) return fmt === 'count' ? v.length : v.length + ' items';
  switch (fmt) {
    case 'percent': return typeof v === 'number' ? Math.round(v <= 1 ? v * 100 : v) + '%' : String(v);
    case 'duration': return typeof v === 'number' ? duration(v) : String(v);
    case 'bytes': return typeof v === 'number' ? bytes(v) : String(v);
    case 'time': return typeof v === 'number' ? clock(v) : String(v);
    case 'count': return typeof v === 'number' ? v.toLocaleString('en-GB') : String(v);
    default: return v;
  }
}

/** Resolves a (possibly bound) value. Lists can be filtered, and mapped to [x, y] points with x / y. */
export function resolve(v, root) {
  if (!isBind(v)) return v;
  let out = walkPath(root, v.$bind);
  if (Array.isArray(out) && v.filter) {
    out = out.filter((row) => Object.keys(v.filter).every((k) => {
      const want = v.filter[k];
      const have = pick(row, k);
      return typeof want === 'string' && typeof have === 'string' ? have.toLowerCase().indexOf(want.toLowerCase()) >= 0 : have === want;
    }));
  }
  if (Array.isArray(out) && (v.x || v.y)) {
    out = out.map((row, i) => [v.x ? pick(row, v.x) : i, Number(pick(row, v.y || 'value'))]).filter((pt) => isFinite(pt[1]));
  }
  if (out === undefined || out === null) return v.fallback !== undefined ? v.fallback : undefined;
  return v.format ? format(out, v.format) : out;
}

/** Collects every binding inside a component (not its children) so a re-render happens only when they change. */
export function bindingSignature(node, root) {
  let sig = '';
  for (const k of Object.keys(node)) {
    if (k === 'children') continue;
    sig += k + ':' + JSON.stringify(deepResolve(node[k], root)) + '|';
  }
  return sig;
}

export function deepResolve(v, root) {
  if (isBind(v)) return resolve(v, root);
  if (Array.isArray(v)) return v.map((x) => deepResolve(x, root));
  if (v && typeof v === 'object') {
    const o = {};
    for (const k of Object.keys(v)) o[k] = deepResolve(v[k], root);
    return o;
  }
  return v;
}

export function hasBinding(v) {
  if (isBind(v)) return true;
  if (Array.isArray(v)) return v.some(hasBinding);
  if (v && typeof v === 'object') return Object.keys(v).some((k) => k !== 'children' && hasBinding(v[k]));
  return false;
}
