/** Before the TV is online: connecting to the FireDev service, or pairing with it. */

import { clear, focusable, h } from '../util.js';
import { brand } from './common.js';

export class CenterScreen {
  constructor(app) {
    this.app = app;
    this.body = h('div.center');
    this.el = h('section.screen', null, h('div.topbar', null, brand()), this.body);
    this.sig = '';
  }

  update() {
    const app = this.app;
    const sig = app.status + (app.pairingCode || '');
    if (sig === this.sig) return;
    this.sig = sig;
    clear(this.body);
    if (app.status === 'pairing' && app.pairingCode) {
      const c = app.pairingCode;
      const digits = c.slice(0, 3).split('').map((d) => h('span', null, d)).concat([h('span.gap')], c.slice(3).split('').map((d) => h('span', null, d)));
      this.body.append(
        h('div.t-meta', null, 'One-time setup'),
        h('div.t-title', { style: 'margin-top:16px' }, 'Pair this TV with FireDev'),
        h('div.code-digits', null, digits),
        h('div.t-body.muted', { style: 'margin-top:48px' }, 'On the machine running FireDev, run:'),
        h('div.terminal', null, h('span.dim', null, '$ '), 'firedev pair ' + c),
        h('div.t-meta', { style: 'margin-top:28px' }, 'The code is valid for 10 minutes.'));
      return;
    }
    const offline = app.status === 'offline';
    this.body.append(
      h('div.t-meta', null, offline ? 'Can’t reach FireDev' : 'Starting'),
      h('div.t-title', { style: 'margin-top:16px' }, offline ? 'Looking for your FireDev service…' : 'Connecting to FireDev…'),
      h('div.terminal', null, app.config.server),
      h('div.t-body.muted', { style: 'margin-top:36px' }, offline
        ? 'Make sure `firedev serve` is running and this TV can reach it. FireDev keeps trying.'
        : 'This takes a moment.'),
      offline ? h('div', { style: 'margin-top:48px' }, focusable(h('button.btn.primary', null, 'Try again'), 'retry', { select: () => app.link.reconnectNow() })) : null);
  }

  back() { this.app.overlays.confirmExit(); }
}
