/**
 * First run in the Fire TV app: choose the machine FireDev works on. Nothing here goes through anyone's cloud.
 *
 *   On this Wi-Fi   computers running `firedev serve` announce themselves; pick one, then pair with a code.
 *   Over SSH        your own server: host and user, the server's fingerprint to confirm (remembered), then the TV's
 *                   own key. Only if the server refuses the key is a password used, once, to add it.
 *
 * The app shell does the connecting; this screen shows what it reports (native "connection" events).
 */

import qrcode from 'qrcode-generator';
import { native } from '../native.js';
import { clear, focusable, h, icon } from '../util.js';
import { brand, hints } from './common.js';

const FORM_KEY = 'firedev.sshForm';

function savedForm() {
  try { return JSON.parse(localStorage.getItem(FORM_KEY)) || {}; } catch (e) { return {}; }
}

function qrSvg(text) {
  const q = qrcode(0, 'L');
  q.addData(text);
  q.make();
  return h('div.qr', { html: q.createSvgTag({ cellSize: 6, margin: 2, scalable: true }) });
}

export class ConnectScreen {
  constructor(app, route) {
    this.app = app;
    this.view = route.view || 'choose'; // choose | ssh | hostkey | key | busy | error | installed
    this.state = null;
    this.body = h('div.connect');
    this.el = h('section.screen.connect-screen', null, h('div.topbar', null, brand(), h('div.grow'),
      h('div.t-meta', null, app.config.device ? 'This TV: ' + app.config.device : '')), this.body,
      hints([['OK', 'Choose'], ['◁', 'Back']]));
    const f = savedForm();
    this.ssh = { host: f.host || '', user: f.user || '', port: f.port || '22', password: '' };
    this.sig = '';
    native.startDiscovery();
  }

  initialFocus() { return null; }

  unmount() { native.stopDiscovery(); }

  /** The shell's progress: connecting, hostkey, needs_key, not_installed, ready, error. */
  onConnection(e) {
    this.state = e;
    if (e.state === 'hostkey') this.view = 'hostkey';
    else if (e.state === 'needs_key') this.view = 'key';
    else if (e.state === 'not_installed') this.view = 'installed';
    else if (e.state === 'connecting') this.view = 'busy';
    else if (e.state === 'error') this.view = 'error';
    this.sig = '';
    this.app.render();
  }

  onFocus(el) {
    const a = document.activeElement;
    if (a && a.tagName === 'INPUT' && !el.contains(a)) a.blur();
  }

  field(id, label, key, opts) {
    opts = opts || {};
    const input = h('input.tv-input', { type: opts.type || 'text', value: this.ssh[key], placeholder: opts.placeholder || '', autocomplete: opts.autocomplete || 'off',
      autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', inputmode: opts.inputmode || null });
    input.addEventListener('input', () => {
      // User names on servers are lower case; TV keyboards capitalise the first letter.
      if (opts.clean) { const v = opts.clean(input.value); if (v !== input.value) input.value = v; }
      this.ssh[key] = input.value;
    });
    return focusable(h('label.tv-field', null, h('span.t-meta', null, label), input, opts.note ? h('span.t-hint', null, opts.note) : null), id,
      { select: () => { input.focus(); try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) { /* number inputs */ } } });
  }

  update() {
    const app = this.app;
    const machines = app.discovered || [];
    const sig = JSON.stringify([this.view, machines.map((m) => [m.key, m.host]), this.state && [this.state.state, this.state.message]]);
    if (sig === this.sig) return;
    this.sig = sig;
    const b = clear(this.body);
    const btn = (id, label, fn, cls) => focusable(h('button.btn' + (cls || ''), null, label), id, { select: fn });
    const st = this.state || {};
    if (this.view === 'choose') {
      b.append(h('div.t-meta', null, 'Set up FireDev'), h('div.t-title', { style: 'margin-top:12px' }, 'Where is your code?'),
        h('div.connect-cols', null,
          h('div.connect-col', null, h('div.t-label', null, 'On this Wi-Fi'),
            machines.length ? h('div.machine-list', null, machines.map((m) => focusable(h('div.card.machine-card', null, icon('code'),
              h('div', null, h('div.t-heading.clip', null, m.name), h('div.t-meta', null, m.host + ' · secure connection'))), 'm-' + m.key,
            { select: () => native.connectLan(m) })))
              : h('div.connect-empty', null, h('span.spinner'), h('div', null, h('div.t-body', null, 'Looking for computers running FireDev…'),
                h('div.t-meta', { style: 'margin-top:14px' }, 'On your computer, in a terminal:'),
                h('div.terminal.small', null, 'firedev init\nfiredev add ~/code/my-app\nfiredev serve')))),
          h('div.connect-col', null, h('div.t-label', null, 'A server you reach over SSH'),
            h('div.t-meta.connect-note', null, 'A VPS or any machine with SSH that this TV can reach. FireDev logs in with this TV’s own key and runs everything there.'),
            btn('ssh', 'Connect over SSH', () => { this.view = 'ssh'; this.sig = ''; app.render(); app.focus.focus('f-host'); }, '.primary'),
            h('div.t-hint.connect-honest', null, 'Away from home? FireDev has no cloud relay: the TV needs to reach your server’s SSH, or join your own VPN (Tailscale, WireGuard), or be on the same Wi-Fi as your computer.'))));
      // The first computer found takes the focus: one OK connects to it.
      if (machines.length && !this.hadMachines) app.focus.focus('m-' + machines[0].key);
      this.hadMachines = machines.length > 0;
      return;
    }
    if (this.view === 'ssh') {
      b.append(h('div.t-meta', null, 'Connect over SSH'), h('div.t-title', { style: 'margin-top:12px' }, 'Your server'),
        h('div.ssh-form', null,
          this.field('f-host', 'Server address', 'host', { placeholder: 'myserver.example.com or 203.0.113.10', inputmode: 'url',
            clean: (v) => v.replace(/\s+/g, '') }),
          this.field('f-user', 'User name', 'user', { placeholder: 'alex', clean: (v) => v.replace(/\s+/g, '').toLowerCase() }),
          this.field('f-port', 'SSH port', 'port', { inputmode: 'numeric', clean: (v) => v.replace(/\D+/g, '') }),
          this.field('f-pass', 'Password (only if this TV’s key isn’t added yet)', 'password', { type: 'password', autocomplete: 'new-password',
            note: 'The TV always tries its own key first. The password is only used if the key is refused, once, to add the key. Then it’s forgotten.' })),
        h('div.t-meta', { style: 'margin-top:14px' }, 'This TV’s key: ' + (native.publicKeyFingerprint() || 'unknown')),
        h('div.connect-actions', null,
          btn('ssh-go', 'Connect', () => this.connectSsh(), '.primary'),
          btn('ssh-key', 'Show this TV’s key', () => { this.view = 'key'; this.state = null; this.sig = ''; app.render(); }),
          btn('ssh-back', 'Back', () => this.back())));
      return;
    }
    if (this.view === 'hostkey') {
      b.append(h('div.t-meta', null, 'First connection to ' + (st.host || 'the server')), h('div.t-title', { style: 'margin-top:12px' }, 'Is this your server?'),
        h('div.t-body.muted', { style: 'margin-top:28px' }, 'Its fingerprint is:'),
        h('div.terminal.fingerprint', null, st.fingerprint || ''),
        h('div.t-body.muted', { style: 'margin-top:20px' }, 'On the server, run this and check the SHA256 part is the same:'),
        h('div.terminal.small', null, 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'),
        h('div.connect-actions', null,
          btn('hk-yes', 'It matches · trust it', () => native.trustHostKey(st.fingerprint), '.primary'),
          btn('hk-no', 'It doesn’t match', () => { this.view = 'ssh'; this.sig = ''; app.render(); })));
      app.focus.focus('hk-no');
      return;
    }
    if (this.view === 'key') {
      const key = native.publicKey() || '';
      const cmd = 'mkdir -p ~/.ssh && echo \'' + key + '\' >> ~/.ssh/authorized_keys';
      b.append(h('div.t-meta', null, 'This TV’s SSH key'), h('div.t-title', { style: 'margin-top:12px' }, 'Let this TV log in'),
        st.state === 'needs_key' ? h('div.t-body.warn', { style: 'margin-top:16px' }, st.message) : null,
        h('div.t-meta', { style: 'margin-top:12px' }, 'Key fingerprint: ' + (native.publicKeyFingerprint() || '') +
          (this.ssh.user ? ' · logging in as ' + this.ssh.user + (this.ssh.host ? '@' + this.ssh.host : '') : '')),
        h('div.key-row', null, qrSvg(cmd),
          h('div.key-text', null, h('div.t-body.muted', null, 'Scan with your phone and paste it into a terminal on your server, as ' + (this.ssh.user || 'your user') + ':'),
            h('div.terminal.small.wrap', null, cmd),
            h('div.t-meta', null, 'The key never leaves this TV except as this public line. Remove it from authorized_keys any time to lock the TV out.'))),
        h('div.connect-actions', null,
          btn('key-retry', 'I’ve added it · connect', () => this.connectSsh(), '.primary'),
          btn('key-pass', 'Use a password instead', () => { this.view = 'ssh'; this.sig = ''; app.render(); app.focus.focus('f-pass'); }),
          btn('key-back', 'Back', () => { this.view = 'ssh'; this.sig = ''; app.render(); })));
      return;
    }
    if (this.view === 'installed') {
      b.append(h('div.t-meta', null, st.host || ''), h('div.t-title', { style: 'margin-top:12px' }, 'FireDev isn’t on that server yet'),
        h('div.t-body.muted', { style: 'margin-top:24px' }, 'This TV logged in fine. Install FireDev there once, then connect again:'),
        h('div.terminal.small', null, 'git clone <FireDev> ~/firedev && cd ~/firedev && tools/setup.sh && tools/install-cli.sh\nfiredev init --no-lan && firedev add ~/code/my-app'),
        h('div.connect-actions', null, btn('inst-retry', 'Connect again', () => this.connectSsh(), '.primary'),
          btn('inst-back', 'Back', () => this.back())));
      return;
    }
    if (this.view === 'busy') {
      b.append(h('div.connect-busy', null, h('span.spinner'), h('div.t-heading', null, st.message || 'Connecting…')));
      return;
    }
    b.append(h('div.t-meta', null, 'Couldn’t connect'), h('div.t-title', { style: 'margin-top:12px' }, st.hostKeyChanged ? 'The server’s identity changed' : 'That didn’t work'),
      h('div.t-body.muted', { style: 'margin-top:24px;max-width:1400px' }, st.message || ''),
      h('div.connect-actions', null, btn('err-retry', 'Try again', () => (this.ssh.host ? this.connectSsh() : this.back()), '.primary'),
        this.ssh.host ? btn('err-edit', 'Change details', () => { this.view = 'ssh'; this.sig = ''; this.app.render(); this.app.focus.focus(st.passwordRefused ? 'f-pass' : 'f-user'); }) : null,
        this.ssh.host ? btn('err-key', 'Show this TV’s key', () => { this.view = 'key'; this.state = null; this.sig = ''; this.app.render(); }) : null,
        btn('err-back', 'Back', () => this.back())));
  }

  connectSsh() {
    const s = this.ssh;
    if (!s.host.trim() || !s.user.trim()) { this.app.toast('Enter the server address and your user name', 'warn'); this.view = 'ssh'; this.sig = ''; this.app.render(); return; }
    s.host = s.host.trim(); s.user = s.user.trim().toLowerCase();
    try { localStorage.setItem(FORM_KEY, JSON.stringify({ host: s.host, user: s.user, port: s.port })); } catch (e) { /* not kept */ }
    native.connectSsh({ host: s.host, user: s.user, port: parseInt(s.port, 10) || 22, password: s.password || null });
    s.password = ''; // used once
  }

  back() {
    if (this.view !== 'choose') { this.view = 'choose'; this.state = null; this.sig = ''; this.app.render(); return; }
    if (this.app.config.server) this.app.navigate('center');
    else this.app.overlays.confirmExit();
  }
}
