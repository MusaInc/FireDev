/** Before the TV is online: connecting to the FireDev service, or pairing with it. */

import { clear, focusable, h, icons, svg } from '../util.js';
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
    const sig = [app.status, app.pairingCode, app.pairingExpired, app.linkDown, app.pairingMismatch, app.pairingMachine].join('|');
    if (sig === this.sig) return;
    this.sig = sig;
    clear(this.body);
    this.el.classList.remove('is-splash');
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
        h('div.t-title', { style: 'margin-top:16px' }, app.pairingMachine ? 'Pair this TV with ' + app.pairingMachine : 'Pair this TV with FireDev'),
        h('div.code-digits', null, digits),
        app.pairingMismatch ? h('div.t-body.bad', { style: 'margin-top:28px;max-width:1500px' },
          'Careful: this connection doesn’t match the computer’s certificate, so something on the network may be in between. The code won’t work; check your network.') : null,
        h('div.t-body.muted', { style: 'margin-top:48px' }, 'On ' + (app.pairingMachine || 'the machine running FireDev') + ', run:'),
        h('div.terminal', null, h('span.dim', null, '$ '), 'firedev pair ' + c),
        this.countdown,
        app.linkDown ? h('div.t-meta', { style: 'margin-top:12px' }, 'Reconnecting to FireDev… the code stays the same.') : null].filter(Boolean));
      this.tick();
      this.timer = setInterval(() => this.tick(), 1000);
      return;
    }
    // Once the service has been unreachable, retries keep the help screen instead of flashing the splash.
    if (app.status === 'offline') this.sawOffline = true;
    const offline = app.status === 'offline' || (this.sawOffline && app.status === 'connecting');
    const conn = app.config.connection;
    if (!offline) {
      // The first thing anyone sees: the brand, big and calm, while the link comes up.
      this.el.classList.add('is-splash');
      this.body.append(h('div.splash', null,
        h('div.splash-mark', null, h('span.splash-glow'), svg(icons.mark)),
        h('div.splash-word.wordmark', null, 'Fire', h('span', null, 'Dev')),
        h('div.splash-tag', null, 'Build software from your sofa. Just say it.'),
        h('div.splash-line', null, h('i')),
        h('div.splash-meta', null, 'Connecting to ' + (conn ? conn.name : 'FireDev') + '…')));
      return;
    }
    this.body.append(...[
      h('div.t-meta', null, offline ? 'Can’t reach FireDev' : 'Starting'),
      h('div.t-title', { style: 'margin-top:16px' }, offline ? 'Looking for your FireDev service…' : 'Connecting to FireDev…'),
      h('div.terminal', null, app.config.connection ? app.config.connection.name + (app.config.connection.mode === 'ssh' ? ' · SSH ' + app.config.connection.user + '@' + app.config.connection.host
        : ' · ' + app.config.connection.host) : app.config.server),
      h('div.t-body.muted', { style: 'margin-top:36px' }, offline
        ? 'Make sure `firedev serve` is running and this TV can reach it. FireDev keeps trying.'
        : 'This takes a moment.'),
      offline ? h('div', { style: 'margin-top:48px;display:flex;gap:24px' }, focusable(h('button.btn.primary', null, 'Try again'), 'retry', { select: () => app.link.reconnectNow() }),
        app.config.transports ? focusable(h('button.btn', null, 'Connect to another machine'), 'change', { select: () => app.changeConnection() }) : null) : null].filter(Boolean));
  }

  back() { this.app.overlays.confirmExit(); }
}
