/**
 * Home: almost nothing. A voice orb, one sentence, one way to start talking. You say what you want and FireDev
 * chooses the project and the screen (see director.js). The projects list is still there for when you'd rather
 * point (☰-less recovery, accessibility): Down, then "Projects", or say "show my projects".
 *
 * The orb and the sentence follow the conversation wherever it comes from: the TV's microphone, the phone
 * microphone page, or FireDev and the agent talking back.
 */

import { clear, focusable, h, svg, icons } from '../util.js';

function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? 'Up late?' : hr < 12 ? 'Good morning.' : hr < 18 ? 'Good afternoon.' : 'Good evening.';
}

export class HomeScreen {
  constructor(app) {
    this.app = app;
    this.orb = h('div.orb', null, h('div.orb-glow'), h('div.orb-ring'), h('div.orb-ring.r2'), h('div.orb-core'));
    this.head = h('div.home-line');
    this.sub = h('div.home-sub');
    this.talk = focusable(h('button.btn.talk', null, svg(icons.mic), h('span', null, 'Start talking')), 'talk', { select: () => this.startTalking() });
    this.work = h('div.home-work');
    this.status = h('div.home-status');
    this.more = focusable(h('button.btn.ghost', null, 'Projects'), 'projects', { select: () => app.navigate('projects') });
    this.el = h('section.screen.home', null,
      h('div.home-top', null, h('div.home-brand', null, svg(icons.mark), 'FireDev'), h('div.grow'), this.status),
      h('div.home-center', null, this.orb, this.head, this.sub, h('div.home-actions', null, this.talk), this.work),
      h('div.home-foot', null, h('div.grow'), this.more));
    this.sig = '';
  }

  initialFocus() { return 'talk'; }

  /** The one button: talk on the TV if it can listen, otherwise on the phone (linking it the first time). */
  startTalking() {
    const app = this.app;
    if (app.voice.listen()) return;
    if ((app.home.voice_links || []).length) {
      app.heard = { state: 'ready', text: '', at: Date.now() };
      app.render();
      return;
    }
    app.overlays.openPhoneLink();
  }

  /** What the orb shows, from the most immediate thing happening. */
  state() {
    const app = this.app;
    const v = app.voice;
    const heard = app.heard && Date.now() - app.heard.at < 12000 ? app.heard : null;
    const busy = (app.home.projects || []).find((p) => p.busy);
    if (v.listening || (heard && heard.state === 'listening')) return { key: 'listening', line: heard && heard.text ? '“' + heard.text + '”' : 'I’m listening.' };
    if (v.speaking) return { key: 'speaking', line: v.speaking.text, who: v.speaking.who };
    if (heard && heard.state === 'understood') return { key: 'thinking', line: '“' + heard.text + '”' };
    if (v.awaiting) return { key: 'thinking', line: 'One moment…' };
    if (heard && heard.state === 'ready') return { key: 'ready', line: 'Go ahead, I’m listening on your phone.' };
    if (busy) return { key: 'working', line: busy.agent + ' is working on ' + lower(busy.task || 'something') + '.' };
    return { key: 'idle', line: greeting() + ' What shall we build?' };
  }

  update() {
    const app = this.app;
    const st = this.state();
    const phones = app.home.voice_links || [];
    const busy = (app.home.projects || []).filter((p) => p.busy);
    const conn = app.config.connection;
    const sig = JSON.stringify([st, phones, busy.map((p) => [p.id, p.task]), app.status, conn && conn.name, app.voice.available()]);
    if (sig === this.sig) return;
    this.sig = sig;
    this.orb.className = 'orb ' + st.key;
    clear(this.head).append(st.line);
    this.head.className = 'home-line' + (st.key === 'speaking' || st.line.length > 60 ? ' long' : '');
    const tvMic = app.voice.available();
    clear(this.sub).append(st.key === 'idle' || st.key === 'working'
      ? (phones.length ? 'Talk on your phone' + (tvMic ? ', or press OK' : '') + '. Ask for anything: a new page, what changed, how something works.'
        : tvMic ? 'Press OK and talk. Ask for anything: a new page, what changed, how something works.'
          : 'Press OK to use your phone as the microphone.')
      : st.who && st.key === 'speaking' ? st.who : '');
    this.talk.querySelector('span').textContent = st.key === 'listening' ? 'Listening…' : phones.length && !tvMic ? 'Talk on my phone' : 'Start talking';
    clear(this.work);
    busy.slice(0, 2).forEach((p) => this.work.appendChild(focusable(h('div.work-chip', null, h('span.dot.live.pulse'),
      h('span.clip', null, p.name + ' · ' + (p.task || 'working'))), 'work-' + p.id, { select: () => app.openProject(p.id) })));
    const online = app.status === 'online';
    clear(this.status).append(
      phones.length ? h('span.home-pill', null, svg(icons.phone), 'Phone microphone') : null,
      h('span.home-pill', null, h('span.dot' + (online ? '.ok' : '.warn')), online ? (conn ? conn.name : 'Connected') : 'Reconnecting…'));
  }

  back() { this.app.overlays.confirmExit(); }
}

function lower(s) { return s ? s[0].toLowerCase() + s.slice(1) : s; }

