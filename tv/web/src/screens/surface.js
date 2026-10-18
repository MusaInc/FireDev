/**
 * The generated screen: whatever an agent built for the current conversation, rendered by FireDev's trusted
 * components, with the conversation alongside it. Live data and patches update parts of it in place; the remote's
 * focus, tabs, chart views and expanded cards survive those updates.
 *
 *   left   the screen itself (scrolls to keep the focused part in view)
 *   right  the conversation: who said what, and whether FireDev is listening, thinking, working or speaking
 */

import { clear, focusable, h, icon } from '../util.js';
import { SurfaceRenderer, LOCAL_ACTIONS } from '../genui/render.js';
import { checkSurface } from '../genui/validate.js';
import { agentPill, connPill } from './common.js';

const CANVAS_W = 1236;
const PROVENANCE = {
  live: ['Live data', 'ok'], agent: ['Gathered by the agent', 'live'], mixed: ['Live + gathered data', 'live'], example: ['Example data', 'warn'],
};

export class SurfaceScreen {
  constructor(app, route) {
    this.app = app;
    this.pid = route.project;
    this.version = -1;
    this.offset = 0;
    this.renderer = new SurfaceRenderer(app, this.pid, { action: (node, action, extra) => this.action(node, action, extra) });
    this.title = h('div.t-heading.clip');
    this.sub = h('div.t-meta.clip');
    this.badges = h('div.g-head-badges');
    this.working = h('div.g-working');
    this.scroller = h('div.g-scroller');
    this.viewport = h('div.g-viewport', null, this.scroller);
    this.rail = h('aside.g-rail');
    this.chips = h('div', { style: 'display:flex;gap:16px;align-items:center' });
    this.back = focusable(h('button.btn.small', null, icon('terminal'), 'Workspace'), 'to-workspace', { select: () => this.toWorkspace() });
    // The top bar comes after the canvas in the DOM (shown above it): the remote starts on the screen's content.
    this.el = h('section.screen.surface', null,
      h('div.g-frame', null, h('div.g-main', null, this.working, this.viewport), this.rail),
      h('div.topbar', null, h('div.grow', null, this.title, this.sub), this.badges, this.chips, this.back));
    this.sigs = {};
  }

  get p() { return this.app.project(this.pid); }

  initialFocus() {
    const first = this.scroller.querySelector('.focusable');
    return first ? first : null;
  }

  update() {
    const entry = this.app.ui.surfaces[this.pid];
    const p = this.p;
    if (!entry) {
      if (this.version !== 0) {
        this.version = 0;
        clear(this.scroller).append(h('div.g-empty', null, h('div.say', null, 'Nothing on screen yet'),
          h('div.t-body.muted', null, 'Ask for something to see: “Show my active coding agents”, “Explain this architecture”, “How are the tests trending?”')));
      }
    } else if (entry.version !== this.version) {
      this.show(entry);
    } else {
      this.renderer.refresh();
    }
    if (p && this.changed('chips', [this.app.status, p.connection, p.agent && p.agent.state])) clear(this.chips).append(connPill(this.app, p), agentPill(p));
    this.renderWorking(p);
    this.renderRail(p);
  }

  changed(k, v) {
    const s = JSON.stringify(v);
    if (this.sigs[k] === s) return false;
    this.sigs[k] = s;
    return true;
  }

  show(entry) {
    const s = entry.surface;
    const err = checkSurface(s);
    const prev = this.version;
    this.version = entry.version;
    clear(this.title).append(s && s.title ? s.title : 'Screen');
    clear(this.sub).append((s && s.subtitle) || '');
    const pv = (s && s.provenance) || {};
    const pinfo = PROVENANCE[pv.kind] || ['Unlabelled data', 'warn'];
    clear(this.badges).append(h('span.pill.g-prov', null, h('span.dot.' + pinfo[1]), pinfo[0] + (pv.sources && pv.sources.length ? ' · ' + pv.sources.slice(0, 2).join(', ') : '')));
    if (err) {
      clear(this.scroller).append(h('div.g-empty', null, h('div.say', null, 'This screen couldn’t be shown'), h('div.t-body.muted', null, err + '. The conversation and terminal still work.')));
      return;
    }
    // A patch re-renders just what changed; anything else (or a gap in versions) redraws the whole screen.
    const patched = entry.patch && prev === entry.patch.base && this.renderer.patch(s, entry.patch.changed);
    if (!patched) {
      const fresh = this.renderer.mount(s, CANVAS_W);
      clear(this.scroller).append(fresh);
      this.scroller.classList.remove('g-enter');
      void this.scroller.offsetWidth;
      this.scroller.classList.add('g-enter');
      this.offset = 0;
      this.scroller.style.transform = '';
      requestAnimationFrame(() => this.makeReachable());
    } else {
      requestAnimationFrame(() => this.makeReachable());
    }
  }

  /** Content taller than the screen must be reachable with the remote: blocks with nothing to focus become stops. */
  makeReachable() {
    const vh = this.viewport.clientHeight;
    if (this.scroller.scrollHeight <= vh + 4) return;
    const top = this.scroller.firstChild;
    const blocks = top && top.classList && (top.classList.contains('g-stack') || top.classList.contains('g-grid')) ? top.children : [top];
    Array.prototype.forEach.call(blocks, (b) => {
      if (b && !b.classList.contains('focusable') && !b.querySelector('.focusable')) focusable(b, 'g-stop-' + (b.dataset.gid || Math.random()), {});
    });
  }

  renderWorking(p) {
    const ut = p && p.ui_task;
    const busy = ut && (ut.status === 'running' || ut.status === 'queued');
    if (!this.changed('working', [busy, ut && ut.status_text])) return;
    clear(this.working);
    this.working.classList.toggle('on', !!busy);
    if (busy) this.working.append(h('span.spinner'), h('span.clip', null, (p.agent && p.agent.name ? p.agent.name : 'The agent') + ' is updating this screen · ' + (ut.status_text || 'Working…')));
  }

  renderRail(p) {
    const conv = (p && p.conversation) || [];
    const voice = this.app.voice;
    const st = voice ? voice.stateFor(p) : { key: 'idle', label: '' };
    if (!this.changed('rail', [conv.map((c) => c.id), st, voice && voice.caption])) return;
    const agentName = (p && p.agent && p.agent.name) || 'Agent';
    const list = h('div.g-conv');
    conv.slice(-9).forEach((c) => list.appendChild(h('div.g-turn.' + c.role + (c.kind === 'progress' ? '.progress' : ''), null,
      h('div.g-who', null, c.role === 'user' ? 'You' : c.role === 'agent' ? agentName : 'FireDev'),
      h('div.g-said', null, c.text))));
    if (!conv.length) list.appendChild(h('div.g-turn.firedev', null, h('div.g-said.dim', null, 'Press ☰ and talk to ' + agentName + '.')));
    clear(this.rail).append(
      h('div.g-state.' + st.key, null, h('span.g-orb'), h('span.clip', null, st.label)),
      voice && voice.caption ? h('div.g-caption', null, voice.caption) : null,
      list,
      h('div.hints', null, h('span', null, h('span.key', null, '☰'), 'Talk'), h('span', null, h('span.key', null, '◁'), 'Back')));
    requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
  }

  onFocus(el) {
    if (el.__tab) el.__tab();
    if (el.__onfocus) el.__onfocus();
    if (!this.viewport.contains(el)) return;
    // Keep the focused part on screen: scroll the canvas, not the page.
    const vr = this.viewport.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const scale = vr.height / this.viewport.offsetHeight || 1;
    const margin = 40 * scale;
    let d = 0;
    if (r.bottom > vr.bottom - margin) d = (r.bottom - vr.bottom + margin) / scale;
    else if (r.top < vr.top + margin) d = (r.top - vr.top - margin) / scale;
    if (!d) return;
    const max = Math.max(0, this.scroller.scrollHeight - this.viewport.offsetHeight);
    this.offset = Math.max(0, Math.min(max, this.offset + d));
    this.scroller.style.transform = 'translateY(' + -this.offset + 'px)';
  }

  /** What happens when a part of the screen is chosen. Local things happen here; the rest goes to the service. */
  action(node, action, extra) {
    const app = this.app;
    if (LOCAL_ACTIONS[action.type]) return this.clientAction(action);
    if (action.type === 'cancel_task') {
      app.overlays.confirm('Stop the current task?', 'The agent stops where it is. You can say “continue” later.', 'Stop it', () => {
        app.command('ui_action', { project: this.pid, node: node.id, item: extra.item, confirmed: true });
      });
      return;
    }
    app.command('ui_action', { project: this.pid, node: node.id, item: extra.item, option: extra.option, values: extra.values }).then((r) => {
      if (r.ok && r.result && r.result.client_action) this.clientAction(r.result.client_action);
      if (r.ok && action.type === 'ask') app.toast('Sent to ' + ((this.p && this.p.agent && this.p.agent.name) || 'the agent'), 'ok');
    });
  }

  clientAction(a) {
    const app = this.app;
    switch (a.type) {
      case 'navigate': return app.go(a.to, this.pid);
      case 'open_diff': return app.navigate('diff', { project: this.pid, path: a.path });
      case 'open_output': return app.overlays.openOutput(this.pid, a.ref, 'Output');
      case 'open_approval': return app.overlays.focusApprove();
      case 'open_preview': return app.overlays.openPreview(a.url);
      case 'expand': {
        const e = this.renderer.entries[a.target];
        if (e) this.renderer.toggle(a.target, true);
        return null;
      }
      case 'select_tab': return this.renderer.selectTab(a.target, a.tab);
      case 'expand_text': {
        // "Expand the Codex agent": a card the TV drew from live data, found by its label.
        const words = String(a.text || '').toLowerCase().split(/\s+/).filter(Boolean);
        const cards = Array.prototype.slice.call(this.scroller.querySelectorAll('[data-label]'));
        const hit = cards.filter((c) => words.every((w) => c.dataset.label.indexOf(w) >= 0 || (w === 'agent' || w === 'agents')))[0];
        if (hit && hit.__expand) {
          hit.__expand(a.op !== 'collapse');
          app.render();
          setTimeout(() => app.focus.focus(hit.dataset.focusId ? hit.dataset.focusId : null), 50);
          app.voice && app.voice.sayLocal((a.op === 'collapse' ? 'Collapsed ' : 'Expanded ') + hit.dataset.label.replace(' · ', ' on ') + '.');
        } else {
          app.toast('I couldn’t find “' + a.text + '” on screen', 'warn');
        }
        return null;
      }
      default: return null;
    }
  }

  /** Live terminal output for terminal components following the latest command. */
  onOutput(ev) {
    const d = ev.data || {};
    Array.prototype.forEach.call(this.scroller.querySelectorAll('.g-codebox.term'), (el) => {
      if (el.__live && el.__live.ref && el.__live.ref === d.run) el.__live.append(d.text || '');
    });
  }

  toWorkspace() { this.app.navigate('workspace', { project: this.pid }); }

  back() { this.toWorkspace(); }
}
