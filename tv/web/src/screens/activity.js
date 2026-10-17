/** Activity: everything the agent and FireDev did, in order. OK on a step shows its raw terminal output. */

import { clear, clock, focusable, h, icon } from '../util.js';
import { agentPill, connPill } from './common.js';

const KIND_ICON = { agent: 'agent', command: 'terminal', file: 'file', tests: 'check', build: 'check', deploy: 'deploy', approval: 'shield', undo: 'undo', notice: 'dot', warning: 'shield' };

export class ActivityScreen {
  constructor(app, route) {
    this.app = app;
    this.pid = route.project;
    this.inner = h('div.list-inner');
    this.list = h('div.list', null, this.inner);
    this.chips = h('div', { style: 'display:flex;gap:20px' });
    this.el = h('section.screen.activity', null,
      h('div.topbar', null, h('div.grow', null, h('div.t-heading', null, 'Activity'), h('div.t-meta', null, 'OK shows the terminal output  ·  ◁ back to the workspace')), this.chips),
      this.list);
    this.sig = '';
    this.offset = 0;
  }

  initialFocus() {
    const rows = this.inner.querySelectorAll('.row');
    return rows.length ? rows[rows.length - 1] : null;
  }

  update() {
    const p = this.app.project(this.pid);
    if (!p) return;
    clear(this.chips).append(connPill(this.app, p), agentPill(p));
    const items = p.activity || [];
    const sig = JSON.stringify(items.map((a) => [a.id, a.status, a.title]));
    if (sig === this.sig) return;
    const atEnd = !this.sig || this.app.focus.focusedEl() === this.inner.lastElementChild;
    this.sig = sig;
    clear(this.inner);
    if (!items.length) {
      this.inner.appendChild(h('div.empty', { style: 'height:700px' }, h('div', null, 'Nothing has happened yet.'), h('div.t-meta', null, 'Ask ' + (p.agent.name || 'the agent') + ' something and every step shows up here.')));
    }
    for (const a of items) {
      const isTask = a.kind === 'task';
      let glyph;
      if (a.status === 'running') glyph = h('span.spinner');
      else if (a.status === 'failed') glyph = icon('cross', 'bad');
      else if (a.status === 'waiting') glyph = icon('shield', 'warn');
      else glyph = icon(KIND_ICON[a.kind] || 'check', a.kind === 'tests' || a.kind === 'build' || a.kind === 'deploy' || a.kind === 'task-end' ? 'ok' : '');
      const right = h('div.right', null,
        a.level >= 1 ? h('span.tag.l' + a.level, null, a.level >= 3 ? 'Destructive' : a.level === 2 ? 'Approval' : 'Edit') : null,
        a.has_output ? h('span.key', null, 'OK') : null);
      const detail = a.kind === 'command' || a.kind === 'approval' ? h('div.detail.mono.clip', null, (a.detail || '').split('\n')[0]) : a.detail && a.kind !== 'agent' ? h('div.detail.clip', null, a.detail.split('\n')[0]) : null;
      const row = h('div.row' + (isTask ? '.task-row' : ''), null,
        h('span.time', null, clock(a.ts)), glyph,
        h('div', { style: 'min-width:0' }, h('div.title' + (a.kind === 'agent' ? '.clamp2' : '.clip'), null, isTask ? 'Task: ' + a.title : a.title), detail),
        right);
      focusable(row, a.id, { select: () => (a.has_output ? this.app.overlays.openOutput(this.pid, a.ref, a.detail || a.title) : a.kind === 'agent' ? this.app.overlays.openText(a.title, a.detail) : null) });
      this.inner.appendChild(row);
    }
    if (atEnd && this.inner.lastElementChild) {
      requestAnimationFrame(() => this.app.focus.focus(this.inner.lastElementChild));
    }
  }

  onFocus(el) {
    // Keep the focused row on screen.
    const listH = this.list.clientHeight;
    const top = el.offsetTop;
    const bottom = top + el.offsetHeight;
    if (top + this.offset < 0) this.offset = -top + 8;
    else if (bottom + this.offset > listH) this.offset = listH - bottom - 8;
    this.offset = Math.min(0, this.offset);
    this.inner.style.transform = 'translateY(' + this.offset + 'px)';
  }

  back() { this.app.navigate('workspace', { project: this.pid }); }
}
