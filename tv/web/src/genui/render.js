/**
 * Renders a generated screen (schema firedev.ui/1) with FireDev's own trusted components. Agent text is only ever
 * set as text, never as markup; URLs were checked by the server. Each component renders inside its own error
 * boundary, keeps its local state (expanded, tab, chart view, scroll) across live updates, and is re-rendered on its
 * own when a patch touches it or its bound data changes, so the rest of the screen and the remote's focus stay put.
 */

import { clear, focusable, h, icon } from '../util.js';
import { bindingRoot, bindingSignature, deepResolve, format, hasBinding, resolve, UNAVAILABLE } from './bind.js';
import { renderChart, seriesColor } from './charts.js';
import { edgePath, layoutGraph, NODE_H, NODE_W } from './graph.js';

const GAP = { s: 16, m: 28, l: 40 };
const PAD = 68; // a boxed component's padding and border, both sides
const CHART_H = { s: 220, m: 300, l: 380, xl: 460 };
const GRAPH_H = { s: 300, m: 400, l: 500, xl: 600 };
const LINES = { s: 6, m: 9, l: 13, xl: 17 };
const ICONS = { check: 1, cross: 1, agent: 1, terminal: 1, file: 1, code: 1, shield: 1, deploy: 1, wait: 1, android: 1, mic: 1, undo: 1, dot: 1 };
const STATUS_ICON = { done: 'check', failed: 'cross', waiting: 'wait', todo: 'dot' };
export const LOCAL_ACTIONS = { navigate: 1, open_diff: 1, open_output: 1, open_approval: 1, expand: 1, select_tab: 1, open_preview: 1 };

const txt = (v) => (v === undefined || v === null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const fid = (id, i) => 'g-' + id + (i !== undefined ? '-' + i : '');

function toneClass(t) { return t && t !== 'neutral' ? ' tone-' + t : ''; }

function iconOf(name, cls) {
  return name && ICONS[name] ? icon(name, cls || '') : null;
}

export class SurfaceRenderer {
  constructor(app, pid, hooks) {
    this.app = app;
    this.pid = pid;
    this.hooks = hooks; // { action(node, action, extra), select(node) }
    this.entries = {}; // id -> { node, el, ctx, sig, bound }
    this.local = { expanded: {}, tab: {}, view: {}, form: {}, scroll: {}, graphFocus: {}, sent: {} };
    this.surface = null;
  }

  root() { return bindingRoot(this.app, this.pid); }

  mount(surface, width) {
    this.surface = surface;
    this.entries = {};
    this.width = width;
    this.data = this.root();
    return this.render(surface.root, { width, depth: 0 });
  }

  /** Re-renders the components a patch changed, in place. */
  patch(surface, changed) {
    const old = this.surface;
    this.surface = surface;
    this.data = this.root();
    if (changed.indexOf('*root') >= 0 || !old) return false;
    for (const id of changed) {
      if (id === '*') continue;
      const e = this.entries[id];
      const node = findNode(surface.root, id);
      if (!e || !node) return false; // structure moved too much: the caller remounts
      this.replace(id, node, e, true);
    }
    return true;
  }

  /** Re-renders components whose bound live data changed. */
  refresh() {
    this.data = this.root();
    for (const id of Object.keys(this.entries)) {
      const e = this.entries[id];
      if (!e.bound || !e.el.parentNode) continue;
      const sig = this.signature(e.node);
      if (sig !== e.sig) this.replace(id, e.node, e, false);
    }
  }

  replace(id, node, e, flash) {
    this.forget(e.node);
    const fresh = this.render(node, e.ctx);
    if (e.el.parentNode) e.el.parentNode.replaceChild(fresh, e.el);
    if (flash) {
      fresh.classList.add('g-updated');
      setTimeout(() => fresh.classList.remove('g-updated'), 900);
    }
  }

  forget(node) {
    if (!node) return;
    delete this.entries[node.id];
    (node.children || []).forEach((c) => this.forget(c));
  }

  signature(node) {
    let sig = bindingSignature(node, this.data);
    if (node.type === 'agent_status') sig += JSON.stringify(node.source === 'agents' ? this.data.agents : [this.data.project.agent, this.data.project.task, this.data.project.ui_task]);
    if (node.type === 'approvals') sig += JSON.stringify(this.data.approvals.map((a) => a.id));
    if (node.type === 'terminal' && node.ref === 'last') sig += JSON.stringify(this.data.project.last_command && this.data.project.last_command.run);
    if (node.type === 'diff' && node.path) sig += JSON.stringify(((this.data.project.changes || {}).files || []).filter((f) => f.path === node.path));
    return sig;
  }

  render(node, ctx) {
    let el;
    try {
      const fn = this['c_' + node.type];
      if (!fn) throw new Error('unknown component ' + node.type);
      const n = node;
      el = fn.call(this, n, deepResolve(withoutChildren(n), this.data), ctx);
      if (n.example) el.appendChild(h('span.g-example', null, 'Example'));
      if (n.size) el.classList.add('size-' + n.size);
    } catch (err) {
      el = h('div.g-broken', null, icon('cross', 'bad'), h('span', null, 'This part (' + node.type + ') couldn’t be shown'));
      if (window.console) console.error('genui', node.type, err);
    }
    el.dataset.gid = node.id;
    const bound = hasBinding(withoutChildren(node)) || ['agent_status', 'approvals', 'terminal', 'diff'].indexOf(node.type) >= 0;
    this.entries[node.id] = { node, el, ctx, bound, sig: bound ? this.signature(node) : '' };
    return el;
  }

  children(node, ctx) {
    return (node.children || []).map((c) => this.render(c, ctx));
  }

  act(node, action, extra) {
    if (action) this.hooks.action(node, action, extra || {});
  }

  // --- layout ---------------------------------------------------------------------------------------------

  c_stack(node, p, ctx) {
    const gap = GAP[p.gap || 'm'];
    const horiz = p.direction === 'horizontal';
    const n = (node.children || []).length || 1;
    const w = horiz ? (ctx.width - gap * (n - 1)) / n : ctx.width;
    const el = h('div.g-stack' + (horiz ? '.horiz' : ''), { style: 'gap:' + gap + 'px' + (p.align ? ';align-items:' + (p.align === 'start' ? 'flex-start' : p.align) : '') });
    this.children(node, { width: w, depth: ctx.depth + 1 }).forEach((c) => el.appendChild(c));
    return el;
  }

  c_grid(node, p, ctx) {
    const cols = p.columns || 2;
    const gap = GAP[p.gap || 'm'];
    const colW = (ctx.width - gap * (cols - 1)) / cols;
    const el = h('div.g-grid', { style: 'gap:' + gap + 'px;grid-template-columns:repeat(' + cols + ',minmax(0,1fr))' });
    for (const c of node.children || []) {
      const span = c.size === 'xl' ? cols : c.size === 'l' ? Math.min(2, cols) : 1;
      const child = this.render(c, { width: colW * span + gap * (span - 1), depth: ctx.depth + 1 });
      if (span > 1) child.style.gridColumn = 'span ' + span;
      el.appendChild(child);
    }
    return el;
  }

  c_tabs(node, p, ctx) {
    const tabs = node.children || [];
    let sel = this.local.tab[node.id];
    if (sel === undefined) sel = Math.min(p.selected || 0, tabs.length - 1);
    const head = h('div.g-tabs-head');
    const body = h('div.g-tabs-body');
    tabs.forEach((t, i) => {
      const b = focusable(h('button.g-tab' + (i === sel ? '.selected' : ''), null, txt(t.label)), fid(node.id, i), {
        select: () => this.selectTab(node.id, i),
      });
      b.__tab = () => this.selectTab(node.id, i);
      head.appendChild(b);
    });
    const cur = tabs[sel];
    if (cur) {
      this.entries[cur.id] = { node: cur, el: body, ctx, bound: false, sig: '' };
      this.children(cur, { width: ctx.width, depth: ctx.depth + 1 }).forEach((c) => body.appendChild(c));
    }
    return h('div.g-tabs', null, head, body);
  }

  selectTab(id, i) {
    if (this.local.tab[id] === i) return;
    this.local.tab[id] = i;
    const e = this.entries[id];
    if (e) this.replace(id, e.node, e, false);
  }

  c_tab(node, p, ctx) { // only reached if a tab is rendered on its own
    const el = h('div.g-stack', { style: 'gap:28px' });
    this.children(node, ctx).forEach((c) => el.appendChild(c));
    return el;
  }

  c_section(node, p, ctx) {
    const el = h('section.g-section', null,
      p.title ? h('div.g-section-title', null, txt(p.title)) : null,
      p.subtitle ? h('div.g-sub', null, txt(p.subtitle)) : null);
    const body = h('div.g-stack', { style: 'gap:24px' });
    this.children(node, { width: ctx.width, depth: ctx.depth + 1 }).forEach((c) => body.appendChild(c));
    el.appendChild(body);
    return el;
  }

  c_card(node, p, ctx) {
    const detail = p.detail ? txt(p.detail) : '';
    const canExpand = !!(p.expandable || detail);
    let open = this.local.expanded[node.id];
    if (open === undefined) open = !!p.expanded;
    const el = h('div.g-card' + toneClass(p.tone) + (open ? '.open' : ''));
    el.append(h('div.g-card-head', null, iconOf(p.icon, 'g-ic'),
      h('div.g-card-titles', null, p.title !== undefined ? h('div.g-card-title.clamp2', null, txt(p.title)) : null,
        p.subtitle ? h('div.g-sub.clamp2', null, txt(p.subtitle)) : null),
      p.badge !== undefined && p.badge !== null ? h('span.g-badge' + toneClass(p.tone), null, txt(p.badge)) : null));
    if (detail && open) el.append(h('div.g-detail', null, detail));
    if (node.children && node.children.length) {
      const body = h('div.g-stack', { style: 'gap:20px' });
      this.children(node, { width: ctx.width - PAD, depth: ctx.depth + 1 }).forEach((c) => body.appendChild(c));
      el.append(body);
    }
    if (canExpand || p.action) {
      if (canExpand && !p.action) el.append(h('div.g-hint', null, open ? 'OK to close' : 'OK for details'));
      focusable(el, fid(node.id), {
        select: () => {
          if (p.action) this.act(node, p.action);
          else this.toggle(node.id, !open);
        },
      });
    }
    return el;
  }

  toggle(id, open) {
    this.local.expanded[id] = open;
    const e = this.entries[id];
    if (e) this.replace(id, e.node, e, false);
  }

  // --- content --------------------------------------------------------------------------------------------

  c_text(node, p) {
    const style = p.style || 'body';
    const el = h('div.g-text.g-' + style + toneClass(p.tone), null, txt(p.text === undefined ? UNAVAILABLE : p.text));
    if (p.max_lines) { el.classList.add('g-clamp'); el.style.webkitLineClamp = String(p.max_lines); }
    return el;
  }

  c_badge(node, p) {
    return h('span.g-badge.solo' + toneClass(p.tone), null, iconOf(p.icon), txt(p.text));
  }

  c_button(node, p) {
    return focusable(h('button.btn.g-button' + (p.tone === 'bad' ? '.danger' : ''), null, iconOf(p.icon), txt(p.label)), fid(node.id), {
      select: () => this.act(node, p.action),
    });
  }

  c_metric(node, p) {
    const value = p.value === undefined || p.value === null ? UNAVAILABLE : txt(p.value);
    const el = h('div.g-metric' + toneClass(p.tone), null,
      h('div.g-metric-label.clip', null, txt(p.label)),
      h('div.g-metric-value', null, h('span.v', null, value), p.unit ? h('span.u', null, txt(p.unit)) : null,
        p.delta !== undefined && p.delta !== null ? h('span.d', null, txt(p.delta)) : null),
      Array.isArray(p.trend) && p.trend.length > 1 ? sparkline(p.trend, p.tone) : null,
      p.caption ? h('div.g-sub.clamp2', null, txt(p.caption)) : null);
    if (p.action) focusable(el, fid(node.id), { select: () => this.act(node, p.action) });
    return el;
  }

  c_progress(node, p) {
    const max = p.max || 100;
    const v = typeof p.value === 'number' ? Math.max(0, Math.min(1, p.value / max)) : null;
    return h('div.g-progress' + toneClass(p.tone), null,
      h('div.g-progress-top', null, h('span.clip', null, txt(p.label)), v !== null && !p.indeterminate ? h('span.g-pct', null, Math.round(v * 100) + '%') : null),
      h('div.bar' + (p.indeterminate || v === null ? '.indeterminate' : '') + (p.tone === 'bad' ? '.bad' : ''), null,
        h('i', { style: 'width:' + (v === null ? 30 : Math.round(v * 100)) + '%' })),
      p.caption ? h('div.g-sub.clip', null, txt(p.caption)) : null);
  }

  items(node, p, timeline) {
    const items = Array.isArray(p.items) ? p.items : [];
    const el = h('div.g-list' + (timeline ? '.timeline' : ''), null, p.title ? h('div.g-block-title', null, txt(p.title)) : null);
    if (!items.length) el.append(h('div.g-sub', null, 'Nothing here yet'));
    items.slice(0, 30).forEach((it, i) => {
      if (!it || typeof it !== 'object') return;
      const key = node.id + ':' + i;
      const open = !!this.local.expanded[key];
      const glyph = it.status === 'running' ? h('span.spinner') : it.status ? icon(STATUS_ICON[it.status] || 'dot', it.status === 'failed' ? 'bad' : it.status === 'done' ? 'ok' : it.status === 'waiting' ? 'warn' : '') : iconOf(it.icon, 'g-ic');
      const row = h('div.g-row' + toneClass(it.tone), null, glyph,
        h('div.g-row-main', null, h('div.g-row-title.clip', null, txt(it.title)),
          it.detail && (open || !it.action) ? h('div.g-sub' + (open ? '' : '.clip'), null, txt(it.detail)) : null),
        it.value !== undefined ? h('span.g-row-value', null, txt(it.value)) : null,
        it.badge !== undefined ? h('span.g-badge' + toneClass(it.tone), null, txt(it.badge)) : null,
        it.time !== undefined ? h('span.g-time', null, txt(it.time)) : null);
      if (it.action || (it.detail && String(it.detail).length > 60)) {
        focusable(row, fid(node.id, i), {
          select: () => {
            if (it.action) this.act(node, it.action, { item: i });
            else { this.local.expanded[key] = !open; const e = this.entries[node.id]; if (e) this.replace(node.id, e.node, e, false); }
          },
        });
      }
      el.appendChild(row);
    });
    if (items.length > 30) el.append(h('div.g-sub', null, '+' + (items.length - 30) + ' more'));
    return el;
  }

  c_list(node, p) { return this.items(node, p, false); }
  c_timeline(node, p) { return this.items(node, p, true); }

  c_table(node, p) {
    const rows = Array.isArray(p.rows) ? p.rows : [];
    const cols = p.columns || [];
    const max = p.max_rows || 8;
    let off = this.local.scroll[node.id] || 0;
    off = Math.max(0, Math.min(off, Math.max(0, rows.length - max)));
    const table = h('div.g-table', { style: 'grid-template-columns:' + cols.map(() => 'minmax(0,1fr)').join(' ') });
    cols.forEach((c) => table.appendChild(h('div.g-th' + (c.align ? '.a-' + c.align : ''), null, txt(c.label))));
    rows.slice(off, off + max).forEach((r) => cols.forEach((c) => {
      const v = r && typeof r === 'object' ? r[c.key] : undefined;
      table.appendChild(h('div.g-td.clip' + (c.align ? '.a-' + c.align : ''), null, txt(c.format ? format(v, c.format) : v === undefined ? UNAVAILABLE : v)));
    }));
    const el = h('div.g-tablewrap', null, p.title ? h('div.g-block-title', null, txt(p.title)) : null, table,
      rows.length > max ? h('div.g-hint', null, 'Rows ' + (off + 1) + '–' + Math.min(rows.length, off + max) + ' of ' + rows.length + '  ·  ▲▼ scroll') : null,
      !rows.length ? h('div.g-sub', null, 'No rows') : null);
    const scroll = (d) => {
      const next = Math.max(0, Math.min(rows.length - max, off + d));
      if (next === off) return false;
      this.local.scroll[node.id] = next;
      const e = this.entries[node.id];
      if (e) this.replace(node.id, e.node, e, false);
      return true;
    };
    return focusable(el, fid(node.id), { up: () => scroll(-3), down: () => scroll(3) });
  }

  c_chart(node, p, ctx) {
    const views = (p.views && p.views.length ? p.views : [p.kind]).slice();
    if (views.indexOf(p.kind) < 0) views.unshift(p.kind);
    const view = this.local.view[node.id] || p.kind;
    const size = node.size || 'm';
    const width = Math.max(320, Math.round(ctx.width - PAD));
    const height = CHART_H[size];
    const series = Array.isArray(p.series) ? p.series.map((s) => Object.assign({}, s, { points: Array.isArray(s.points) ? s.points : [] })) : [];
    const legend = series.length > 1 && view !== 'pie'
      ? h('div.g-legend-row', null, series.map((s, i) => h('span', null, h('i', { style: 'background:' + seriesColor(s, i) }), txt(s.name))))
      : null;
    const el = h('div.g-chartbox', null,
      h('div.g-chart-head', null, h('div.g-block-title.clip', null, txt(p.title || '')),
        views.length > 1 ? h('div.g-views', null, views.map((v) => h('span' + (v === view ? '.on' : ''), null, v))) : null),
      renderChart(view, series, { width, height, labels: p.labels, unit: p.unit }), legend,
      p.caption ? h('div.g-sub', null, txt(p.caption)) : null);
    return focusable(el, fid(node.id), {
      select: () => {
        if (views.length < 2) return;
        this.local.view[node.id] = views[(views.indexOf(view) + 1) % views.length];
        const e = this.entries[node.id];
        if (e) this.replace(node.id, e.node, e, false);
      },
    });
  }

  c_graph(node, p, ctx) {
    const nodes = Array.isArray(p.nodes) ? p.nodes : [];
    const L = layoutGraph(nodes, p.edges || [], p.direction || 'right');
    const boxW = ctx.width - PAD;
    const boxH = GRAPH_H[node.size || 'm'];
    const scale = Math.min(1, boxW / L.width, boxH / L.height);
    const inner = h('div.g-graph-inner', { style: 'width:' + L.width + 'px;height:' + L.height + 'px;transform:scale(' + scale + ');' +
      'left:' + Math.max(0, (boxW - L.width * scale) / 2) + 'px;top:' + Math.max(0, (boxH - L.height * scale) / 2) + 'px' });
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('width', L.width);
    svg.setAttribute('height', L.height);
    svg.setAttribute('style', 'width:' + L.width + 'px;height:' + L.height + 'px');
    svg.setAttribute('class', 'g-edges');
    for (const e of p.edges || []) {
      const a = L.boxes[e.from];
      const b = L.boxes[e.to];
      if (!a || !b) continue;
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', edgePath(a, b, L.across));
      path.setAttribute('class', 'g-edge' + (e.style === 'dashed' ? ' dashed' : ''));
      svg.appendChild(path);
      if (e.label) {
        const t = document.createElementNS(NS, 'text');
        t.setAttribute('x', (a.x + b.x + NODE_W) / 2);
        t.setAttribute('y', (a.y + b.y + NODE_H) / 2 - 10);
        t.setAttribute('text-anchor', 'middle');
        t.setAttribute('class', 'g-edge-label');
        t.textContent = String(e.label).slice(0, 40);
        svg.appendChild(t);
      }
    }
    inner.appendChild(svg);
    const detail = h('div.g-graph-detail');
    const show = (n) => {
      clear(detail);
      if (n) detail.append(h('b', null, txt(n.label)), n.detail ? h('span', null, '  ' + txt(n.detail)) : h('span.dim', null, n.group ? '  ' + txt(n.group) : ''));
    };
    nodes.forEach((n) => {
      const b = L.boxes[n.id];
      if (!b) return;
      const box = h('div.g-node' + toneClass(n.tone), { style: 'left:' + b.x + 'px;top:' + b.y + 'px;width:' + NODE_W + 'px;height:' + NODE_H + 'px' },
        iconOf(n.icon, 'g-ic'), h('div', { style: 'min-width:0' }, h('div.g-node-label.clamp2', null, txt(n.label)), n.group ? h('div.g-node-group.clip', null, txt(n.group)) : null));
      focusable(box, fid(node.id, 'n-' + n.id), { select: () => show(n) });
      box.__onfocus = () => { this.local.graphFocus[node.id] = n.id; show(n); };
      inner.appendChild(box);
    });
    const focusedNode = nodes.filter((n) => n.id === this.local.graphFocus[node.id])[0];
    show(focusedNode || null);
    if (!focusedNode) detail.append(h('span.dim', null, '◀ ▶ ▲ ▼ to explore the diagram'));
    return h('div.g-graph', null, p.title ? h('div.g-block-title', null, txt(p.title)) : null,
      h('div.g-graph-box', { style: 'height:' + boxH + 'px' }, inner), detail);
  }

  scrollBox(node, inner, lines) {
    const lineH = 40;
    const box = h('div.g-scrollbox', { style: 'height:' + (lines * lineH + 24) + 'px' }, inner);
    const scroll = (d) => {
      const max = Math.max(0, inner.scrollHeight - box.clientHeight + 24);
      const cur = this.local.scroll[node.id] || 0;
      const next = Math.max(0, Math.min(max, cur + d * lineH * 3));
      if (next === cur) return false;
      this.local.scroll[node.id] = next;
      inner.style.transform = 'translateY(' + -next + 'px)';
      return true;
    };
    if (this.local.scroll[node.id]) inner.style.transform = 'translateY(' + -this.local.scroll[node.id] + 'px)';
    return { box, scroll };
  }

  c_code(node, p) {
    const lines = String(p.code || '').split('\n');
    const start = p.start_line || 1;
    const hl = {};
    (p.highlight || []).forEach((n) => { hl[n] = true; });
    const pre = h('div.g-code-inner', null, lines.slice(0, 600).map((l, i) => h('div.g-line' + (hl[start + i] ? '.hl' : ''), null,
      h('span.ln', null, String(start + i)), h('span', null, l || ' '))));
    const { box, scroll } = this.scrollBox(node, pre, LINES[node.size || 'm']);
    const el = h('div.g-codebox', null, h('div.g-chart-head', null, h('div.g-block-title.clip', null, txt(p.title || '')), p.language ? h('span.g-badge', null, txt(p.language)) : null), box);
    return focusable(el, fid(node.id), { up: () => scroll(-1), down: () => scroll(1) });
  }

  c_diff(node, p) {
    const inner = h('div.g-code-inner', null, h('div.g-line.dim', null, 'Loading the diff…'));
    const { box, scroll } = this.scrollBox(node, inner, LINES[node.size || 'm']);
    const fill = (hunks) => {
      clear(inner);
      if (!hunks.length) { inner.append(h('div.g-line.dim', null, 'No changes')); return; }
      hunks.forEach((hk) => {
        inner.append(h('div.g-line.hunk', null, '@@ ' + (hk.header || '')));
        hk.lines.slice(0, 400).forEach((l) => inner.append(h('div.g-line' + (l.t === '+' ? '.add' : l.t === '-' ? '.del' : ''), null,
          h('span.ln', null, String(l.t === '-' ? l.old : l.new || '')), h('span', null, (l.t === ' ' ? ' ' : l.t) + ' ' + l.text))));
      });
    };
    if (p.text) fill(parseDiff(String(p.text)));
    else if (p.path) {
      this.app.link.command('get_file_diff', { project: this.pid, path: p.path }).then((r) => {
        if (r.ok && r.result && r.result.diff) fill(r.result.diff.hunks || []);
        else { clear(inner); inner.append(h('div.g-line.dim', null, (r && r.error) || 'Couldn’t load the diff')); }
      });
    }
    const el = h('div.g-codebox', null, h('div.g-chart-head', null, h('div.g-block-title.clip', null, txt(p.title || p.path || 'Changes'))), box);
    return focusable(el, fid(node.id), {
      up: () => scroll(-1), down: () => scroll(1),
      select: () => { if (p.path) this.act(node, { type: 'open_diff', path: p.path }); },
    });
  }

  c_terminal(node, p) {
    const inner = h('div.g-code-inner.term');
    const lines = LINES[node.size || 'm'];
    const { box, scroll } = this.scrollBox(node, inner, lines);
    const proj = this.data.project || {};
    const lc = proj.last_command;
    const ref = p.ref === 'last' ? (lc && lc.run) || null : p.ref;
    const setText = (t) => {
      clear(inner);
      String(t || '').split('\n').slice(-300).forEach((l) => inner.appendChild(h('div.g-line', null, l || ' ')));
      requestAnimationFrame(() => { inner.style.transform = 'translateY(' + -Math.max(0, inner.scrollHeight - box.clientHeight + 24) + 'px)'; });
    };
    if (p.text) setText(p.text);
    else if (ref) {
      setText('Loading…');
      this.app.link.command('get_output', { project: this.pid, ref }).then((r) => setText((r.ok && r.result.output) || 'No output yet.'));
    } else setText(lc ? '$ ' + lc.command + '\n' + (lc.latest || '') : 'No commands yet.');
    const el = h('div.g-codebox.term', null, h('div.g-chart-head', null,
      h('div.g-block-title.clip.mono', null, txt(p.title || (lc && p.ref === 'last' ? '$ ' + lc.command : 'Terminal'))),
      lc && p.ref === 'last' && lc.running ? h('span.spinner') : null), box);
    el.__live = { ref, append: (text) => { String(text).split('\n').forEach((l) => { if (l) inner.appendChild(h('div.g-line', null, l)); }); while (inner.childNodes.length > 400) inner.removeChild(inner.firstChild); inner.style.transform = 'translateY(' + -Math.max(0, inner.scrollHeight - box.clientHeight + 24) + 'px)'; } };
    return focusable(el, fid(node.id), {
      up: () => scroll(-1), down: () => scroll(1),
      select: () => { if (ref) this.act(node, { type: 'open_output', ref }); },
    });
  }

  c_image(node, p) {
    return h('figure.g-image', null, h('img', { src: p.src, alt: p.alt || '' }), p.caption ? h('figcaption.g-sub', null, txt(p.caption)) : null);
  }

  c_preview(node, p, ctx) {
    const w = ctx.width - PAD;
    const scale = w / 1280;
    const frame = h('iframe', { src: p.url, tabindex: '-1', style: 'width:1280px;height:720px;transform:scale(' + scale + ')', sandbox: 'allow-scripts allow-same-origin' });
    const el = h('div.g-preview', null, h('div.g-chart-head', null, h('div.g-block-title.clip', null, txt(p.title || 'Live preview')), h('span.g-badge.tone-good', null, 'Live')),
      h('div.g-preview-box', { style: 'height:' + Math.round(720 * scale) + 'px' }, frame),
      h('div.g-sub.clip', null, txt(p.caption || p.url.replace(/^https?:\/\//, ''))));
    return focusable(el, fid(node.id), { select: () => this.act(node, { type: 'open_preview', url: p.url }) });
  }

  c_choice(node, p) {
    const sent = this.local.sent[node.id];
    const el = h('div.g-choice', null, p.prompt ? h('div.g-card-title', null, txt(p.prompt)) : null);
    const row = h('div.g-choice-row');
    (p.options || []).forEach((o, i) => {
      const b = focusable(h('button.btn.g-option' + (sent === i ? '.chosen' : ''), null, h('span', null, txt(o.label)), o.detail ? h('span.g-option-detail', null, txt(o.detail)) : null), fid(node.id, i), {
        select: () => {
          this.local.sent[node.id] = i;
          this.act(node, { type: 'ask', server: true }, { option: i });
          const e = this.entries[node.id];
          if (e) this.replace(node.id, e.node, e, false);
        },
      });
      row.appendChild(b);
    });
    el.append(row, sent !== undefined ? h('div.g-hint', null, 'Sent: ' + txt(((p.options || [])[sent] || {}).label)) : null);
    return el;
  }

  c_form(node, p) {
    const st = this.local.form[node.id] = this.local.form[node.id] || {};
    const fields = p.fields || [];
    fields.forEach((f) => { if (st[f.key] === undefined) st[f.key] = f.value !== undefined ? f.value : f.kind === 'toggle' ? false : f.kind === 'number' ? (f.min || 0) : (f.options || [])[0]; });
    const rerender = () => { const e = this.entries[node.id]; if (e) this.replace(node.id, e.node, e, false); };
    const el = h('div.g-form', null, p.title ? h('div.g-block-title', null, txt(p.title)) : null, p.prompt ? h('div.g-sub', null, txt(p.prompt)) : null);
    fields.forEach((f, fi) => {
      const row = h('div.g-field', null, h('div.g-field-label', null, txt(f.label)));
      if (f.kind === 'choice') {
        (f.options || []).forEach((o, oi) => row.appendChild(focusable(h('button.btn.small.g-option' + (st[f.key] === o ? '.chosen' : ''), null, txt(o)), fid(node.id, fi + '-' + oi), {
          select: () => { st[f.key] = o; rerender(); } })));
      } else if (f.kind === 'toggle') {
        row.appendChild(focusable(h('button.btn.small.g-toggle' + (st[f.key] ? '.chosen' : ''), null, st[f.key] ? 'On' : 'Off'), fid(node.id, fi), {
          select: () => { st[f.key] = !st[f.key]; rerender(); } }));
      } else {
        const step = f.step || 1;
        const bump = (d) => { const v = Number(st[f.key]) + d * step; if ((f.min !== undefined && v < f.min) || (f.max !== undefined && v > f.max)) return true; st[f.key] = Math.round(v * 1000) / 1000; rerender(); return true; };
        row.appendChild(focusable(h('div.g-stepper', null, h('span', null, '◀'), h('b', null, String(st[f.key])), h('span', null, '▶')), fid(node.id, fi), {
          left: () => bump(-1), right: () => bump(1) }));
      }
      el.appendChild(row);
    });
    el.appendChild(focusable(h('button.btn.primary.g-submit', null, txt(p.submit || 'Send')), fid(node.id, 'submit'), {
      select: () => this.act(node, { type: 'ask', server: true }, { values: Object.assign({}, st) }) }));
    return el;
  }

  // --- live components --------------------------------------------------------------------------------------

  c_agent_status(node, p) {
    const el = h('div.g-agents', null, p.title ? h('div.g-block-title', null, txt(p.title)) : null);
    let list;
    if (p.source === 'agents') {
      list = (this.data.agents || []).slice();
      if (p.filter) list = list.filter((a) => JSON.stringify(a).toLowerCase().indexOf(String(p.filter).toLowerCase()) >= 0);
      const updated = this.app.ui && this.app.ui.data && this.app.ui.data.agents_updated;
      if (!updated && !list.length) { el.append(h('div.g-sub', null, 'Looking for agents on the machine…')); return el; }
    } else {
      const pr = this.data.project || {};
      const a = pr.agent || {};
      list = [{ agent: a.name, project: pr.name, state: pr.connection !== 'connected' ? 'offline' : a.state, task: pr.task && pr.task.title,
        status_text: pr.task && pr.task.status_text, presenter: pr.ui_task && pr.ui_task.title, source: 'firedev', machine: pr.machine }];
    }
    if (!list.length) el.append(h('div.g-sub', null, 'No coding agents are running.'));
    const grid = h('div.g-grid', { style: 'gap:24px;grid-template-columns:repeat(' + Math.min(3, Math.max(1, list.length)) + ',minmax(0,1fr))' });
    list.slice(0, 12).forEach((a, i) => {
      const key = node.id + ':' + (a.pid || a.firedev_project || i);
      const open = !!this.local.expanded[key];
      const working = a.state === 'working';
      const label = (a.agent || 'Agent') + ' · ' + (a.project || '?');
      const card = h('div.g-card.g-agent' + (working ? '.tone-info' : a.state === 'offline' ? '.dimmed' : '') + (open ? '.open' : ''), null,
        h('div.g-card-head', null, icon('agent', 'g-ic' + (working ? ' live' : '')),
          h('div.g-card-titles', null, h('div.g-card-title.clip', null, label),
            h('div.g-sub.clip', null, a.task ? a.task : a.source === 'firedev' ? 'FireDev · ' + (a.machine || '') : (a.mode || '') + (a.managed_by ? ' · via FireDev' : ''))),
          h('span.g-badge' + (working ? '.tone-info' : ''), null, working ? h('span.dot.live.pulse') : null, a.state || '?')),
        a.status_text ? h('div.g-sub.clip', { style: 'margin-top:12px' }, a.status_text) : null,
        a.presenter ? h('div.g-sub.clip', null, 'Presenter: ' + a.presenter) : null,
        open ? h('div.g-detail', null, [a.cwd ? 'Folder: ' + a.cwd : null, a.pid ? 'Process ' + a.pid : null,
          a.uptime_s !== undefined ? 'Running for ' + format(a.uptime_s, 'duration') : null, a.cpu !== undefined ? 'CPU ' + a.cpu + '%' : null,
          a.mem_mb !== undefined ? a.mem_mb + ' MB' : null].filter(Boolean).join('  ·  ')) : null);
      card.dataset.label = label.toLowerCase();
      card.__expand = (want) => { this.local.expanded[key] = want; const e = this.entries[node.id]; if (e) this.replace(node.id, e.node, e, false); };
      focusable(card, fid(node.id, i), { select: () => card.__expand(!open) });
      grid.appendChild(card);
    });
    el.appendChild(grid);
    if (list.length > 12) el.append(h('div.g-sub', null, '+' + (list.length - 12) + ' more'));
    return el;
  }

  c_approvals(node, p) {
    const list = this.data.approvals || [];
    const el = h('div.g-list', null, h('div.g-block-title', null, txt(p.title || 'Waiting for your approval')));
    if (!list.length) el.append(h('div.g-row', null, icon('check', 'ok'), h('div.g-row-main', null, h('div.g-row-title', null, 'Nothing is waiting for approval'))));
    list.forEach((a, i) => el.appendChild(focusable(h('div.g-row.tone-warn', null, icon('shield', 'warn'),
      h('div.g-row-main', null, h('div.g-row-title.clip', null, a.title), h('div.g-sub.clip.mono', null, a.command)),
      h('span.tag.l' + a.level, null, 'Level ' + a.level)), fid(node.id, i), { select: () => this.act(node, { type: 'open_approval', approval: a.id }) })));
    return el;
  }
}

function withoutChildren(node) {
  const o = {};
  for (const k of Object.keys(node)) if (k !== 'children') o[k] = node[k];
  return o;
}

export function findNode(node, id) {
  if (!node || typeof node !== 'object') return null;
  if (node.id === id) return node;
  for (const c of node.children || []) {
    const hit = findNode(c, id);
    if (hit) return hit;
  }
  return null;
}

function sparkline(values, tone) {
  const NS = 'http://www.w3.org/2000/svg';
  const w = 240;
  const hh = 56;
  const max = Math.max.apply(null, values);
  const min = Math.min.apply(null, values);
  const pts = values.map((v, i) => (i * w) / (values.length - 1) + ',' + (hh - 4 - ((v - min) / (max - min || 1)) * (hh - 8)));
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + w + ' ' + hh);
  svg.setAttribute('class', 'g-spark' + toneClass(tone));
  const pl = document.createElementNS(NS, 'polyline');
  pl.setAttribute('points', pts.join(' '));
  svg.appendChild(pl);
  return svg;
}

function parseDiff(text) {
  const hunks = [];
  let cur = null;
  let o = 0;
  let n = 0;
  text.split('\n').forEach((line) => {
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(line);
    if (m) { o = +m[1]; n = +m[2]; cur = { header: m[3].trim(), lines: [] }; hunks.push(cur); return; }
    if (!cur || /^(\+\+\+|---|\\)/.test(line)) return;
    if (line[0] === '+') cur.lines.push({ t: '+', text: line.slice(1), new: n++ });
    else if (line[0] === '-') cur.lines.push({ t: '-', text: line.slice(1), old: o++ });
    else { cur.lines.push({ t: ' ', text: line.slice(1), old: o++, new: n++ }); }
  });
  return hunks;
}
