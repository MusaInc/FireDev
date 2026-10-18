/** Before the TV is online: connecting to the FireDev service, or pairing with it. */

import { clear, focusable, h } from '../util.js';
import { brand } from './common.js';

export class CenterScreen {
  constructor(app) {
    this.app = app;
    this.body = h('div.center');
    this.el = h('section.screen', null, h('div.topbar', null, brand()), this.body);
    this.sig = '';
    this.countdown = null;
    this.timer = 0;
  }

  /** "Valid for 9:41 more" — ticks without redrawing the screen, and flips to the expired view at zero. */
  tick() {
    const app = this.app;
    const left = Math.max(0, Math.round((app.pairingExpiresAt - Date.now()) / 1000));
    if (left === 0 && !app.pairingExpired) { app.pairingExpired = true; app.render(); return; }
    if (this.countdown) this.countdown.textContent = 'The code is valid for ' + Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2) + ' more.';
  }

  unmount() { clearInterval(this.timer); }

  update() {
    const app = this.app;
    const sig = [app.status, app.pairingCode, app.pairingExpired, app.linkDown].join('|');
    if (sig === this.sig) return;
    this.sig = sig;
    clear(this.body);
    clearInterval(this.timer);
    this.countdown = null;
    if (app.status === 'pairing' && app.pairingCode && app.pairingExpired) {
      this.body.append(
        h('div.t-meta', null, 'One-time setup'),
        h('div.t-title', { style: 'margin-top:16px' }, 'That pairing code has expired'),
        h('div.t-body.muted', { style: 'margin-top:36px' }, 'Codes last 10 minutes. Get a new one, then run the command it shows.'),
        h('div', { style: 'margin-top:48px' }, focusable(h('button.btn.primary', null, 'Get a new code'), 'new-code', { select: () => app.link.newCode() })));
      return;
    }
    if (app.status === 'pairing' && app.pairingCode) {
      const c = app.pairingCode;
      const digits = c.slice(0, 3).split('').map((d) => h('span', null, d)).concat([h('span.gap')], c.slice(3).split('').map((d) => h('span', null, d)));
      this.countdown = h('div.t-meta.pair-countdown', { style: 'margin-top:28px' });
      this.body.append(...[
        h('div.t-meta', null, 'One-time setup'),
        h('div.t-title', { style: 'margin-top:16px' }, 'Pair this TV with FireDev'),
        h('div.code-digits', null, digits),
        h('div.t-body.muted', { style: 'margin-top:48px' }, 'On the machine running FireDev, run:'),
        h('div.terminal', null, h('span.dim', null, '$ '), 'firedev pair ' + c),
        this.countdown,
        app.linkDown ? h('div.t-meta', { style: 'margin-top:12px' }, 'Reconnecting to FireDev… the code stays the same.') : null].filter(Boolean));
      this.tick();
      this.timer = setInterval(() => this.tick(), 1000);
      return;
    }
    const offline = app.status === 'offline';
    this.body.append(...[
      h('div.t-meta', null, offline ? 'Can’t reach FireDev' : 'Starting'),
      h('div.t-title', { style: 'margin-top:16px' }, offline ? 'Looking for your FireDev service…' : 'Connecting to FireDev…'),
      h('div.terminal', null, app.config.server),
      h('div.t-body.muted', { style: 'margin-top:36px' }, offline
        ? 'Make sure `firedev serve` is running and this TV can reach it. FireDev keeps trying.'
        : 'This takes a moment.'),
      offline ? h('div', { style: 'margin-top:48px' }, focusable(h('button.btn.primary', null, 'Try again'), 'retry', { select: () => app.link.reconnectNow() })) : null].filter(Boolean));
  }

  back() { this.app.overlays.confirmExit(); }
}
