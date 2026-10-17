/** Changes: changed files on the left, a readable diff of the focused file on the right (→ to scroll it). */

import { clear, focusable, h, plural } from '../util.js';
import { connPill } from './common.js';

const LINE_H = 42;

export class DiffScreen {
  constructor(app, route) {
    this.app = app;
    this.pid = route.project;
    this.files = h('div.files');
    this.body = h('div.diff-body');
    this.title = h('div.t-meta');
    this.pane = focusable(h('div.pane', null, this.body), 'pane', {
      up: () => this.scroll(-6), down: () => this.scroll(6), left: () => { this.app.focus.focus('file-' + this.current); return true; }, right: () => true,
    });
    this.chips = h('div');
    this.el = h('section.screen.diff', null,
      h('div.topbar', null, h('div.grow', null, h('div.t-heading', null, 'What changed'), this.title), this.chips),
      h('div.diff-layout', null, this.files, this.pane));
    this.sig = '';
    this.current = null;
    this.offset = 0;
    this.cache = {};
    this.app.command('get_changes', { project: this.pid });
  }

  initialFocus() { return this.current ? 'file-' + this.current : null; }

  update() {
    const p = this.app.project(this.pid);
    if (!p) return;
    clear(this.chips).appendChild(connPill(this.app, p));
    const c = p.changes || { files: [] };
    const sig = JSON.stringify(c.files);
    if (sig === this.sig) return;
    this.sig = sig;
    this.cache = {};
    clear(this.title).appendChild(document.createTextNode(c.files.length ? plural(c.files.length, 'file') + ' changed  ·  +' + c.added + '  −' + c.removed + '  ·  uncommitted' : 'No uncommitted changes'));
    clear(this.files);
    if (!c.files.length) {
      clear(this.body).appendChild(h('div.empty', { style: 'height:600px' }, h('div', null, 'Nothing has changed yet.'), h('div.t-meta', null, 'Changes the agent makes show up here.')));
      return;
    }
    for (const f of c.files) {
      this.files.appendChild(focusable(h('div.file', { 'data-path': f.path }, h('div.name.clip', null, f.name), h('div.path.clip', null, f.path),
        h('div.counts', null, h('span.ok', null, '+' + f.added), h('span.bad', null, '−' + f.removed), f.status === 'added' ? h('span.dim', null, 'new file') : null)),
      'file-' + f.path, { select: () => this.app.focus.focus('pane'), right: () => { this.app.focus.focus('pane'); return true; } }));
    }
    if (!this.current || !c.files.some((f) => f.path === this.current)) this.show(c.files[0].path);
  }

  onFocus(el) {
    const path = el.dataset.path;
    if (path && path !== this.current) this.show(path);
    for (const f of this.files.children) f.classList.toggle('current', f.dataset.path === this.current);
  }

  show(path) {
    this.current = path;
    this.offset = 0;
    this.body.style.transform = '';
    for (const f of this.files.children) f.classList.toggle('current', f.dataset.path === path);
    if (this.cache[path]) { this.render(this.cache[path]); return; }
    clear(this.body).appendChild(h('div.empty', { style: 'height:600px' }, h('span.spinner')));
    this.app.link.command('get_file_diff', { project: this.pid, path }).then((r) => {
      if (!r.ok || this.current !== path) return;
      this.cache[path] = r.result.diff;
      this.render(r.result.diff);
    });
  }

  render(diff) {
    const b = clear(this.body);
    if (!diff.hunks.length) { b.appendChild(h('div.empty', { style: 'height:600px' }, diff.message || 'No changes')); return; }
    for (const hk of diff.hunks) {
      b.appendChild(h('div.hunk-head', null, '@@ line ' + hk.new_start + (hk.header ? '  ·  ' + hk.header : '')));
      for (const ln of hk.lines) {
        const cls = ln.t === '+' ? '.add' : ln.t === '-' ? '.del' : '';
        b.appendChild(h('div.dl' + cls, null, h('span.n', null, ln.old || ''), h('span.n', null, ln.new || ''), h('span.s', null, ln.t === ' ' ? '' : ln.t === '-' ? '−' : '+'), h('span.c', null, ln.text)));
      }
      if (hk.truncated) b.appendChild(h('div.hunk-head', null, '… more lines not shown'));
    }
  }

  scroll(lines) {
    const max = Math.max(0, this.body.scrollHeight - this.pane.clientHeight + 40);
    this.offset = Math.max(-max, Math.min(0, this.offset - lines * LINE_H));
    this.body.style.transform = 'translateY(' + this.offset + 'px)';
    return true;
  }

  back() {
    if (this.app.focus.focusedId() === 'pane') { this.app.focus.focus('file-' + this.current); return; }
    this.app.navigate('workspace', { project: this.pid });
  }
}
