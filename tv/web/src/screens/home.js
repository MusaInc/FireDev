/** Home: the configured projects as large cards. */

import { clear, focusable, h, icon } from '../util.js';
import { brand, connPill, hints } from './common.js';

function statusOf(p) {
  if (p.connection === 'error') return { dot: 'bad', text: 'Can’t connect' };
  if (p.connection === 'reconnecting') return { dot: 'warn', text: 'Reconnecting' };
  if (p.connection !== 'connected') {
    if (p.connection === 'disconnected') return { dot: '', text: 'Ready to connect' };
    return { dot: 'live', text: 'Connecting' };
  }
  if (p.busy) return { dot: 'live', text: 'Working · ' + (p.task || '') };
  if (p.build === 'failed') return { dot: 'bad', text: 'Build failing' };
  if (p.agent_state === 'unavailable') return { dot: 'bad', text: p.agent + ' not installed' };
  return { dot: 'ok', text: 'Ready' };
}

export class HomeScreen {
  constructor(app, route) {
    this.app = app;
    this.route = route;
    this.pill = h('div');
    this.cards = h('div.project-row');
    this.el = h('section.screen.home', null,
      h('div.topbar', null, brand(), h('div.grow'), this.pill),
      h('div.home-hero', null, h('div.t-display', null, 'FireDev'), h('div.tagline', null, 'Develop from anywhere.')),
      this.cards,
      h('div', { style: 'flex:1' }),
      hints([['OK', 'Open project'], ['☰', 'Speak'], ['◁', 'Exit']]));
    this.sig = '';
  }

  initialFocus() {
    const pre = this.route.preselect || this.app.focusProject;
    return pre ? 'project-' + pre : null;
  }

  update() {
    const app = this.app;
    clear(this.pill).appendChild(connPill(app, null));
    const projects = app.home.projects || [];
    const sig = JSON.stringify(projects.map((p) => [p.id, statusOf(p), p.machine, p.agent, p.name]));
    if (sig === this.sig) return;
    this.sig = sig;
    clear(this.cards);
    for (const p of projects) {
      const st = statusOf(p);
      const glyph = /android|kotlin|gradle/i.test(p.name + ' ' + (p.description || '') + ' ' + (p.aliases || []).join(' ')) ? 'android' : 'code';
      this.cards.appendChild(focusable(h('div.card.project-card', null,
        h('div.icon', null, icon(glyph)),
        h('div.t-heading.clip', null, p.name),
        h('dl.facts', null,
          h('dt', null, 'Machine'), h('dd.clip', null, p.machine),
          h('dt', null, 'Agent'), h('dd', null, p.agent),
          h('dt', null, 'Status'), h('dd', null, h('span.dot' + (st.dot ? '.' + st.dot : '')), h('span.clip', null, st.text)))),
      'project-' + p.id, { select: () => app.openProject(p.id) }));
    }
    this.cards.appendChild(focusable(h('div.card.add-card', null,
      icon('plus'),
      h('div.t-label', null, 'Phone microphone'),
      h('div.t-meta', { style: 'margin-top:10px' }, 'Scan to speak from your phone')), 'phone-mic', { select: () => app.overlays.openPhoneLink() }));
  }

  back() {
    this.app.overlays.confirmExit();
  }
}
