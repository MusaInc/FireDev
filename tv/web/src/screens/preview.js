/**
 * Live preview: the project's real running web app next to the agent, or full screen.
 *
 *   split   the agent's current work on the left, the page on the right (four sizes)
 *   full    the page edge to edge; Back returns to split
 *
 * The page is a sandboxed frame on the preview gateway (its own origin). The frame never takes the remote: OK on the
 * page starts "using the page" (arrows and OK go to the page's own links and buttons), Back stops. Desktop, tablet and
 * mobile viewports are real CSS widths, so the page's own responsive layout is what you see; zoom changes that width.
 */

import { liveState, STAGES } from '../live.js';
import { nowPanel } from './livebits.js';
import { CodePanel } from '../codepanel.js';
import { clear, focusable, h, icon } from '../util.js';
import { SPLITS, VIEWPORTS, ZOOMS } from '../preview.js';
import { agentPill, connPill } from './common.js';

const SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-pointer-lock';
const SPLIT_GAP = 32;
const CODE_W = 500; // the live code column
const INNER_W = 1920 - 96 * 2;

export class PreviewScreen {
  constructor(app, route) {
    this.app = app;
    this.isPreview = true;
    this.pid = route.project;
    this.route = route;
    this.prefs = app.preview.prefs(this.pid);
    this.st = app.preview.state(this.pid);
    this.frame = null;
    this.frameOrigin = null;
    this.ready = false;
    this.content = null; // how much the page has drawn (null: not reported yet)
    this.next = null; // the page reloading off screen, swapped in once it has loaded (no white flash)
    this.interacting = false;
    this.ticketing = false;
    this.page = null;
    this.title = '';
    this.errors = 0;
    this.focusLabel = '';
    this.screenshots = !!route.screenshots;
    this.retries = 0;
    this.sigs = {};

    this.head = h('div.grow');
    this.chips = h('div.pv-chips');
    this.agent = focusable(h('div.card.pv-agent'), 'agent', { select: () => this.close() });
    this.statusEl = h('div.pv-status');
    this.banner = h('div.pv-banner');
    this.revealEl = h('div.pv-reveal-label');
    this.device = h('div.pv-device');
    this.box = focusable(h('div.pv-box', null, this.device, this.statusEl, this.banner, this.revealEl,
      h('div.pv-fullhint', null, 'OK: use the page  ·  ↓ Controls  ·  ◁ Split screen')), 'frame', {
      select: () => this.interact(!this.interacting),
    });
    this.sink = h('button.pv-sink', { tabindex: '-1', 'aria-hidden': 'true' });
    this.toolbar = h('div.pv-toolbar');
    this.buttons = {};
    const tb = (id, label, fn, cls) => {
      const b = focusable(h('button.btn.small' + (cls || ''), null, label), 'tb-' + id, { select: fn });
      this.buttons[id] = b;
      return b;
    };
    this.toolbar.append(
      tb('back', [icon('undo'), 'Back to ' + this.agentName()], () => this.close()),
      tb('layout', '', () => this.setLayout(this.prefs.layout === 'full' ? 'split' : 'full')),
      tb('view', '', () => this.cycleViewport()),
      tb('zoom-out', '−', () => this.zoom(-1), '.pv-icon'),
      tb('zoom', '', () => this.zoom(0), '.pv-zoom'),
      tb('zoom-in', '+', () => this.zoom(1), '.pv-icon'),
      tb('size', '', () => this.cycleSplit()),
      tb('reload', 'Reload', () => this.reloadFrame('reload')),
      tb('use', 'Use the page', () => { this.app.focus.focus('frame'); this.interact(true); }));
    // Live code: the real code behind each change, replayed beside the page (see codepanel.js).
    this.code = new CodePanel(this.agentName());
    this.el = h('section.screen.preview-screen', null,
      h('div.topbar', null, this.head, this.chips),
      h('div.pv-main', null, this.agent, this.box, this.code.el),
      this.toolbar, this.sink);
    this.onBlur = () => setTimeout(() => this.reclaim(), 0);
    this.ticker = setInterval(() => { if (!this.gone) this.update(); }, 5000); // the quiet line moves on by itself
    window.addEventListener('blur', this.onBlur);
    this.applyPrefs(true);
    // Opened mid-build: replay what the agent just wrote (labelled with how long ago), newest last.
    const recent = (app.code[this.pid] || []).filter((c) => Date.now() - c.ts < 120000).slice(-2);
    recent.forEach((c) => this.code.enqueue(c.data, c.ts));
    this.connect({ start: route.start, path: route.path, screenshots: this.screenshots });
    this.report(true);
  }

  initialFocus() { return 'frame'; }

  /** Full screen shows the controls only while the remote is on them. */
  onFocus(el) {
    this.el.classList.toggle('pv-tools', !!(el && el.dataset.focusId && el.dataset.focusId.indexOf('tb-') === 0));
  }

  agentName() {
    const p = this.app.project(this.pid);
    return (p && p.agent && p.agent.name) || 'the agent';
  }

  // --- the service -------------------------------------------------------------------------------------

  connect(opts) {
    opts = opts || {};
    this.ticketing = true;
    this.app.command('preview_open', { project: this.pid, base: this.app.config.server, preview_base: this.app.config.previewBase, start: !!opts.start, install: !!opts.install,
      path: opts.path || this.page || null, screenshots: !!opts.screenshots, dir: opts.dir }).then((r) => {
      this.ticketing = false;
      if (this.gone) return;
      const res = r.result || {};
      if (res.preview) { this.st = res.preview; this.app.preview.states[this.pid] = res.preview; }
      this.notice = r.ok ? null : (r.error || res.message || 'The preview could not be opened');
      if (res.url) this.loadFrame(res.url, res.origin);
      else if (this.frame && (!this.st || this.st.status !== 'running')) this.dropFrame();
      this.update();
    });
  }

  reconnect(opts) {
    if (opts && opts.screenshots) this.screenshots = true;
    this.dropFrame();
    this.connect(Object.assign({ screenshots: this.screenshots }, opts || {}));
  }

  onState(st) {
    const was = this.st && this.st.status;
    this.st = st;
    if (st.status === 'running' && !this.frame && !this.ticketing && !this.gone && this.retries <= 3) this.connect({ screenshots: this.screenshots });
    else if (st.status !== 'running' && st.status !== 'starting' && this.frame && !this.screenshots) this.dropFrame();
    if (was !== st.status) this.app.render();
    else this.update();
  }

  report(visible) {
    this.app.link.command('preview_view', { project: this.pid, visible, layout: this.prefs.layout, viewport: this.prefs.viewport });
  }

  // --- the frame -----------------------------------------------------------------------------------------

  loadFrame(url, origin) {
    this.dropFrame();
    this.frameOrigin = origin ? origin.replace(/\/$/, '') : null;
    this.ready = false;
    this.refused = false;
    this.frame = h('iframe.pv-frame', { src: url, sandbox: SANDBOX, tabindex: '-1', referrerpolicy: 'no-referrer', title: 'Live preview',
      allow: 'clipboard-write; fullscreen' });
    this.frame.addEventListener('load', () => {
      // Pages without the helper (a JSON route, an image) still count as loaded.
      setTimeout(() => { if (!this.ready && this.frame && !this.refused) { this.ready = true; this.update(); } }, 2500);
      this.reclaim();
    });
    this.device.appendChild(this.frame);
    this.layout();
    this.update();
  }

  dropFrame() {
    if (this.frame) this.frame.remove();
    this.dropNext();
    this.frame = null;
    this.ready = false;
    this.content = null;
    if (this.interacting) this.interact(false);
  }

  /** The page must never keep the keyboard, except a field you chose with OK while using the page. */
  reclaim() {
    if (this.gone || !this.frame) return;
    if (document.activeElement === this.frame && !(this.interacting && this.typing)) {
      try { this.sink.focus(); } catch (e) { /* nothing to focus */ }
    }
  }

  post(cmd) {
    if (!this.frame || !this.frame.contentWindow) return false;
    try { this.frame.contentWindow.postMessage(Object.assign({ fd: 'cmd' }, cmd), this.frameOrigin || '*'); } catch (e) { return false; }
    return true;
  }

  onMessage(e) {
    if (this.frameOrigin && e.origin !== this.frameOrigin) return;
    const m = e.data || {};
    if (this.next && e.source === this.next.contentWindow) return this.onNextMessage(m);
    if (!this.frame || e.source !== this.frame.contentWindow) return;
    switch (m.fd) {
      case 'content':
        this.content = Number(m.n) || 1;
        this.update();
        break;
      case 'css':
        if (!m.n) this.swapReload(); // no stylesheet to swap: reload the page instead
        break;
      case 'hello':
      case 'nav':
        if (m.fd === 'hello') this.content = typeof m.content === 'number' ? m.content : 1;
        this.ready = true;
        this.retries = 0;
        this.page = typeof m.page === 'string' ? m.page.slice(0, 300) : this.page;
        this.title = typeof m.title === 'string' ? m.title.slice(0, 120) : '';
        clearTimeout(this.navTimer);
        this.navTimer = setTimeout(() => this.app.link.command('preview_report', { project: this.pid, page: this.page, title: this.title }), 400);
        this.update();
        break;
      case 'error':
        this.errors += 1;
        this.app.link.command('preview_report', { project: this.pid, error: String(m.message || '').slice(0, 400) });
        this.update();
        break;
      case 'key':
        if (m.action === 'back') { this.interact(false); this.reclaim(); }
        break;
      case 'focus':
        this.focusLabel = String(m.label || '').slice(0, 80);
        this.typing = m.tag === 'input' || m.tag === 'textarea' || m.tag === 'select';
        this.renderBanner();
        break;
      case 'notfound':
        if (this.pendingPath) { this.post({ op: 'go', path: this.pendingPath }); this.pendingPath = null; }
        else this.app.toast('Couldn’t find “' + m.text + '” on the page', 'warn');
        break;
      case 'clicked':
        this.pendingPath = null;
        break;
      case 'expired':
        // FireDev restarted or the session ended: get a fresh address, a few times at most. If even a fresh one is
        // refused, this TV isn't keeping the preview's session cookie (plain HTTP next to the app's local files).
        this.refused = true;
        this.ready = false;
        if (this.retries++ < 3) setTimeout(() => { if (!this.gone) this.reconnect(); }, 600 * this.retries);
        else {
          this.dropFrame();
          this.notice = location.protocol === 'file:' && !/^https:/.test(this.frameOrigin || '')
            ? 'This TV can only keep the preview’s session over HTTPS. Put FireDev and its preview gateway behind HTTPS (server.preview_url).'
            : 'The preview gateway keeps refusing this TV’s session. Check that this TV is still paired.';
          this.update();
        }
        break;
      case 'down':
        this.down = true;
        this.update();
        break;
      default:
        break;
    }
  }

  /** The agent saved something. Stylesheets swap in place; anything else reloads off screen and swaps in when it
   *  has loaded, so the page on the TV never goes blank between versions. */
  reloadFrame(reason, opts) {
    opts = opts || {};
    if (!this.frame) { this.connect({ screenshots: this.screenshots }); return; }
    if (!this.ready) { if (!this.post({ op: 'reload' })) this.reconnect(); return; }
    if (opts.css) this.post({ op: 'css' });
    else this.swapReload();
    if (reason && reason !== 'reload') {
      this.flash('Updated · ' + reason.replace(/ changed$/, ''));
      this.box.classList.remove('pv-pulse');
      void this.box.offsetWidth;
      this.box.classList.add('pv-pulse');
    }
  }

  swapReload() {
    if (!this.frame || !this.frameOrigin || this.screenshots) { if (!this.post({ op: 'reload' })) this.reconnect(); return; }
    this.post({ op: 'savescroll' });
    this.dropNext();
    const next = h('iframe.pv-frame.pv-next', { src: this.frameOrigin + (this.page || '/'), sandbox: SANDBOX, tabindex: '-1',
      referrerpolicy: 'no-referrer', title: 'Live preview (updating)', allow: 'clipboard-write; fullscreen' });
    this.next = next;
    next.addEventListener('load', () => this.reclaim());
    // A version that never says hello (a server mid-restart, an expired session) falls back to a plain reload.
    this.nextTimer = setTimeout(() => { if (this.next === next) { this.dropNext(); if (!this.post({ op: 'reload' })) this.reconnect(); } }, 9000);
    this.device.appendChild(next);
  }

  onNextMessage(m) {
    if (m.fd === 'expired' || m.fd === 'down') { this.dropNext(); if (!this.post({ op: 'reload' })) this.reconnect(); return; }
    if (m.fd !== 'hello') return;
    const next = this.next;
    this.next = null;
    clearTimeout(this.nextTimer);
    if (this.frame) this.frame.remove();
    next.classList.remove('pv-next');
    this.frame = next;
    this.ready = true;
    this.content = typeof m.content === 'number' ? m.content : 1;
    this.page = typeof m.page === 'string' ? m.page.slice(0, 300) : this.page;
    this.title = typeof m.title === 'string' ? m.title.slice(0, 120) : this.title;
    this.reclaim();
    this.update();
  }

  dropNext() {
    clearTimeout(this.nextTimer);
    if (this.next) this.next.remove();
    this.next = null;
  }

  flash(text) {
    this.flashText = text;
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => { this.flashText = null; this.update(); }, 2600);
    this.update();
  }

  // --- reveals --------------------------------------------------------------------------------------------------

  /** The page edge to edge for a moment ("First look", "Done"), then back to the split. Not a preference: any key
   *  ends it, and it never starts while the remote is in use. */
  reveal(label, ms) {
    if (this.gone || this.interacting || this.prefs.layout === 'full') return;
    if (this.app.director && this.app.director.handsOn()) return; // someone is driving: don't take the screen away
    this.revealing = { label };
    this.code.hold(true);
    this.el.classList.add('pv-reveal');
    clear(this.revealEl).append(h('span.pv-reveal-dot'), h('span', null, label));
    clearTimeout(this.revealTimer);
    this.revealTimer = setTimeout(() => this.endReveal(), ms);
    this.layout();
    this.update();
  }

  endReveal() {
    if (!this.revealing) return;
    this.revealing = null;
    this.code.hold(false);
    clearTimeout(this.revealTimer);
    this.el.classList.remove('pv-reveal');
    this.sigs = {};
    this.layout();
    this.update();
  }

  /** Milestones worth the whole screen: the page's first appearance in a build, and the agent finishing. */
  watchMilestones(p) {
    const t = p.task || p.last_task;
    const drawn = this.ready && this.content !== 0 && !!this.frame;
    if (drawn && !this.firstLook && t && t.kind === 'agent' && (t.status === 'running' || t.status === 'queued')) {
      this.firstLook = true;
      setTimeout(() => {
        this.reveal('First look · ' + (this.title || p.name), 7500);
        // Said only if it can be said now, while the page is on show: late, it would be noise.
        const v = this.app.voice;
        if (this.revealing && v && !v.speaking && !v.queue.length) v.sayLocal('Here’s the first look.');
      }, 900);
    }
    if (drawn) this.firstLook = true;
    if (t && t.kind === 'agent' && t.lane !== 'ui') {
      const was = this.lastTaskState;
      this.lastTaskState = t.id + ':' + t.status;
      if (was && was !== this.lastTaskState && was.split(':')[0] === t.id && t.status === 'completed') {
        // After the last reload has had a moment to land.
        setTimeout(() => this.reveal('Done · ' + t.title, 9000), 2200);
      }
    }
  }

  // --- remote ----------------------------------------------------------------------------------------------

  interact(on) {
    this.interacting = !!on && !!this.frame && this.ready;
    this.el.classList.toggle('pv-using', this.interacting);
    if (this.interacting) this.post({ op: 'ring', show: true });
    else { this.typing = false; this.post({ op: 'ring', show: false }); }
    this.renderBanner();
  }

  key(action, down) {
    if (this.revealing && down) { this.endReveal(); return action === 'back' || action === 'select'; }
    if (!down) return action === 'select' && this.interacting;
    const onFrame = this.app.focus.focusedId() === 'frame';
    if (this.interacting) {
      if (action === 'back') { this.interact(false); return true; }
      if (['up', 'down', 'left', 'right', 'select'].indexOf(action) >= 0) { this.post({ op: 'key', action }); return true; }
      if (action === 'rewind') { this.post({ op: 'back' }); return true; }
      if (action === 'forward') { this.post({ op: 'forward' }); return true; }
      return false;
    }
    if (onFrame && action === 'rewind') { this.post({ op: 'back' }); return true; }
    if (onFrame && action === 'forward') { this.post({ op: 'forward' }); return true; }
    return false;
  }

  back() {
    if (this.prefs.layout === 'full') this.setLayout('split');
    else this.close();
  }

  close() { this.app.go('workspace', this.pid); }

  onCode(change, ts) { this.code.enqueue(change, ts); this.layout(); }

  /** Three columns (agent, page, code) while there's code to show or the agent is building; two otherwise. */
  codeVisible() {
    if (this.prefs.layout === 'full' || this.revealing) return false;
    const p = this.app.project(this.pid);
    const t = p && (p.task || p.last_task);
    return !!((this.app.code[this.pid] || []).length || (t && t.kind === 'agent' && (t.status === 'running' || t.status === 'queued')));
  }

  unmount() {
    this.code.destroy();
    this.gone = true;
    clearInterval(this.ticker);
    clearTimeout(this.revealTimer);
    window.removeEventListener('blur', this.onBlur);
    clearTimeout(this.flashTimer);
    clearTimeout(this.navTimer);
    this.dropFrame();
    this.report(false);
  }

  // --- view options -----------------------------------------------------------------------------------------

  applyPrefs(first) {
    this.prefs = this.app.preview.prefs(this.pid);
    this.el.classList.toggle('pv-full', this.prefs.layout === 'full');
    if (!first) this.report(true);
    this.layout();
    this.update();
  }

  setLayout(layout) {
    this.app.preview.setPrefs(this.pid, { layout });
    this.applyPrefs();
    this.app.focus.restore();
  }

  cycleViewport(to) {
    const order = ['desktop', 'mobile', 'tablet'];
    const next = to || order[(order.indexOf(this.prefs.viewport) + 1) % order.length];
    this.app.preview.setPrefs(this.pid, { viewport: next });
    this.applyPrefs();
  }

  zoom(dir) {
    let i = ZOOMS.indexOf(this.prefs.zoom);
    i = dir === 0 ? ZOOMS.indexOf(1) : Math.max(0, Math.min(ZOOMS.length - 1, i + dir));
    this.app.preview.setPrefs(this.pid, { zoom: ZOOMS[i] });
    this.applyPrefs();
  }

  cycleSplit(dir) {
    if (this.prefs.layout === 'full') {
      if (dir < 0) this.setLayout('split');
      return;
    }
    let i = this.prefs.split;
    if (dir === undefined) i = (i + 1) % SPLITS.length;
    else if (dir > 0 && i === SPLITS.length - 1) return this.setLayout('full');
    else i = Math.max(0, Math.min(SPLITS.length - 1, i + dir));
    this.app.preview.setPrefs(this.pid, { split: i });
    this.applyPrefs();
    return null;
  }

  /** Voice controls from the service. */
  control(a) {
    switch (a.op) {
      case 'viewport': return this.cycleViewport(a.viewport);
      case 'zoom': return this.zoom(a.zoom === 'in' ? 1 : a.zoom === 'out' ? -1 : 0);
      case 'size': return this.cycleSplit(a.size === 'bigger' ? 1 : -1);
      case 'nav':
        if (a.nav === 'reload') return this.reloadFrame('reload');
        if (a.nav === 'scroll') return this.post({ op: 'scroll', dir: a.dir || 'down' });
        if (a.nav === 'top' || a.nav === 'bottom' || a.nav === 'back' || a.nav === 'forward') return this.post({ op: a.nav });
        if (a.nav === 'go') return this.post({ op: 'go', path: a.path });
        if (a.nav === 'click') { this.pendingPath = a.path || null; return this.post({ op: 'click', text: a.text }); }
        return null;
      default: return null;
    }
  }

  /** Sizes the page's frame: a real CSS viewport (desktop, tablet, mobile) scaled to fit the box. */
  layout() {
    const full = this.prefs.layout === 'full' || !!this.revealing;
    const three = this.codeVisible();
    this.el.classList.toggle('pv-3col', three);
    this.code.el.style.display = three ? '' : 'none';
    const pw = Math.round((INNER_W - SPLIT_GAP * (three ? 2 : 1) - (three ? CODE_W : 0)) * SPLITS[this.prefs.split]);
    this.box.style.width = full ? '' : pw + 'px';
    this.agent.style.display = full ? 'none' : '';
    const W = full ? 1920 : pw;
    const H = full ? 1080 : (this.box.clientHeight || 760);
    const vp = VIEWPORTS[this.prefs.viewport];
    const z = this.prefs.zoom;
    const d = this.device;
    if (!vp.device) {
      const vw = Math.round(vp.w / z);
      const scale = W / vw;
      const vh = Math.round(H / scale);
      d.className = 'pv-device desktop';
      d.style.cssText = 'width:' + vw + 'px;height:' + vh + 'px;transform:scale(' + scale + ');left:0;top:0';
    } else {
      const vw = Math.round(vp.w / z);
      const vh = Math.round(vp.h / z);
      const bezel = 18;
      const scale = Math.min((W - 48) / (vw + bezel * 2), (H - 48) / (vh + bezel * 2));
      const ow = (vw + bezel * 2) * scale;
      const oh = (vh + bezel * 2) * scale;
      d.className = 'pv-device phone';
      d.style.cssText = 'width:' + vw + 'px;height:' + vh + 'px;transform:scale(' + scale + ');left:' + Math.round((W - ow) / 2 + bezel * scale) + 'px;top:' + Math.round((H - oh) / 2 + bezel * scale) + 'px';
    }
    if (!this.box.clientHeight && !full) requestAnimationFrame(() => { if (!this.gone && this.box.clientHeight) this.layout(); });
  }

  // --- rendering ----------------------------------------------------------------------------------------------

  changed(key, value) {
    const s = JSON.stringify(value);
    if (this.sigs[key] === s) return false;
    this.sigs[key] = s;
    return true;
  }

  update() {
    const app = this.app;
    const p = app.project(this.pid);
    if (!p) return;
    const st = this.st || app.preview.state(this.pid) || { status: 'unknown' };
    const full = this.prefs.layout === 'full';
    if (this.changed('head', [p.name, st.label, st.status, this.title, this.page])) {
      clear(this.head).append(h('div.t-heading.clip', null, p.name + '  ·  Live preview'),
        h('div.t-meta.clip', null, (this.title ? this.title + '  ·  ' : '') + (this.page || st.path || '/') + (st.label ? '  ·  ' + st.label + ' on ' + p.machine : '')));
    }
    if (this.changed('chips', [app.status, p.connection, st.status, st.hmr, this.errors, this.flashText, p.agent && p.agent.state])) {
      const live = st.status === 'running';
      clear(this.chips).append(
        this.flashText ? h('span.pill.ok', null, h('span.dot.ok'), this.flashText) : null,
        this.errors ? h('span.pill.bad', null, h('span.dot.bad'), this.errors + (this.errors === 1 ? ' page error' : ' page errors')) : null,
        h('span.pill' + (live ? '.ok' : ''), null, h('span.dot' + (live ? '.ok' : st.status === 'starting' ? '.live.pulse' : st.status === 'failed' ? '.bad' : '')),
          live ? (st.hmr ? 'Live · hot reload' : 'Live · reloads on save') : statusWord(st.status)),
        connPill(app, p));
    }
    const b = this.buttons;
    b.layout.textContent = full ? 'Split screen' : 'Full screen';
    b.view.textContent = VIEWPORTS[this.prefs.viewport].label + ' view';
    b.zoom.textContent = Math.round(this.prefs.zoom * 100) + '%';
    b.size.textContent = 'Size ' + (this.prefs.split + 1) + '/' + SPLITS.length;
    b.size.style.display = full ? 'none' : '';
    b.use.style.display = this.frame && this.ready ? '' : 'none';
    this.renderAgent(p);
    const live = liveState(p);
    this.code.setAgent(live && (live.running || live.waiting || live.task.kind === 'agent') ? live : null, this.agentName());
    if (this.codeVisible() !== this.el.classList.contains('pv-3col')) this.layout();
    this.renderStatus(st, p);
    this.renderBanner();
    this.watchMilestones(p);
  }

  renderBanner() {
    const el = clear(this.banner);
    this.banner.style.display = this.interacting ? '' : 'none';
    if (!this.interacting) return;
    el.append(h('span.pv-banner-dot'), h('span', null, this.typing ? 'Typing in the page' : 'Using the page'),
      this.focusLabel ? h('span.pv-banner-label.clip', null, '· ' + this.focusLabel) : null,
      h('span.pv-banner-hint', null, 'Arrows move · OK clicks · ◁ Back returns to FireDev'));
  }

  renderAgent(p) {
    if (this.prefs.layout === 'full' || this.revealing) return;
    const t = p.task || p.last_task;
    const live = liveState(p);
    const said = (p.conversation || []).filter((c) => c.role === 'agent').slice(-1)[0];
    const quiet = live && live.quiet >= 15 ? live.quiet - (live.quiet % 5) : 0;
    if (!this.changed('agent', [t && t.id, t && t.status, live && [live.now, live.stage, live.said, live.steps.map((s) => [s.id, s.status])], said && said.id, p.agent && p.agent.state, quiet])) return;
    const c = clear(this.agent);
    const running = live && (live.running || live.waiting);
    c.classList.toggle('is-live', !!running);
    c.append(h('div.pv-agent-head', null, agentPill(p)));
    if (!t) {
      c.append(h('div.t-label', { style: 'margin-top:28px' }, 'Ask for a change'),
        h('div.t-meta', { style: 'margin-top:12px' }, 'Say “Make the header smaller” and watch it change here.'));
    } else {
      const pct = (live.stage / (STAGES.length - 1)) * 100;
      c.append(h('div.t-meta', { style: 'margin-top:26px' }, running ? 'Working on' : t.status === 'completed' ? 'Done' : 'Last task'),
        h('div.pv-agent-title.clamp2', null, t.title),
        h('div.pv-rail', null, h('div.pv-rail-track', null, h('i', { style: 'width:' + pct + '%' })),
          h('div.pv-rail-label', null, STAGES[live.stage].label + '  ·  ' + (live.stage + 1) + ' of ' + STAGES.length)));
      if (running) c.append(h('div.pv-now', null, nowPanel(live.now, true, quiet)));
      if (running && live.said) c.append(h('div.pv-quote.clamp3', null, '“' + live.said + '”'));
      const steps = live.steps.slice(running ? -3 : -4);
      if (steps.length) c.append(h('div.pv-steps', null, steps.map((s) => h('div.pv-step', null,
        s.status === 'running' ? h('span.spinner') : icon(s.status === 'failed' && !s.soft ? 'cross' : s.icon || 'check', s.status === 'failed' && !s.soft ? 'bad' : ''), h('span.clip', null, s.past || s.object)))));
    }
    if (said && !running) c.append(h('div.pv-said.clamp4', null, said.text));
    c.append(h('div.pv-agent-foot.t-hint', null, 'OK: back to ' + this.agentName() + '  ·  ☰ Speak'));
  }

  renderStatus(st, p) {
    const s = st.status;
    const showFrame = !!this.frame && (s === 'running' || this.screenshots) && !this.down;
    // Never a blank page: the frame shows once the page has loaded and drawn something.
    const drawn = this.ready && this.content !== 0;
    const key = [s, st.message, (st.log || []).slice(-8), st.needs_install, this.notice, showFrame, this.ready, drawn, this.down, (st.alternatives || []).length, p.connection];
    this.device.style.visibility = showFrame && drawn ? 'visible' : 'hidden';
    if (!this.changed('status', key)) return;
    const el = clear(this.statusEl);
    if (showFrame && drawn) { el.style.display = 'none'; return; }
    el.style.display = '';
    const btn = (id, label, fn, cls) => focusable(h('button.btn.small' + (cls || ''), null, label), id, { select: fn });
    const log = (st.log || []).length ? h('div.pv-log.mono', null, st.log.slice(-9).map((l) => h('div.clip', null, l))) : null;
    if (p.connection !== 'connected') {
      el.append(h('span.spinner'), h('div.t-label', null, 'Connecting to ' + p.machine + '…'));
    } else if (this.down) {
      el.append(h('span.spinner'), h('div.t-label', null, 'Reconnecting to the preview…'), h('div.t-meta', null, 'The page comes back as soon as the server answers.'));
      setTimeout(() => { if (!this.gone && this.down) { this.down = false; this.reconnect(); } }, 3000);
    } else if (showFrame && this.ready) {
      el.append(h('span.spinner'), h('div.t-label', null, 'The page is up, with nothing on it yet'),
        h('div.t-meta', null, 'It appears here the moment ' + this.agentName() + ' puts something on it.'));
    } else if (showFrame || (s === 'running' && (this.ticketing || this.frame))) {
      el.append(h('span.spinner'), h('div.t-label', null, 'Opening the page…'));
    } else if (this.notice) {
      el.append(icon('cross', 'bad'), h('div.t-label', null, 'The preview couldn’t open'), h('div.t-meta.pv-msg', null, this.notice));
    } else if (s === 'starting') {
      const approving = /approval/i.test(st.message || '');
      el.append(approving ? icon('shield', 'warn') : h('span.spinner'), h('div.t-label', null, st.message || 'Starting the dev server…'),
        approving ? h('div.t-meta', null, 'It runs your project’s own code, so FireDev asks first.') : null, log);
    } else if (s === 'idle') {
      el.append(h('div.t-label', null, (st.label || 'Web') + ' project · the preview isn’t running'),
        h('div.t-meta.pv-msg', null, st.needs_install ? 'Its dependencies aren’t installed. FireDev will ask before installing them.' : 'FireDev starts the dev server in your project’s tmux session, on ' + p.machine + ' only.'),
        h('div.pv-actions', null, st.needs_install
          ? btn('pv-install', 'Install dependencies and start', () => this.reconnect({ start: true, install: true }), '.primary')
          : btn('pv-start', 'Start the preview', () => this.reconnect({ start: true }), '.primary')));
    } else if (s === 'failed') {
      el.append(icon('cross', 'bad'), h('div.t-label', null, 'The preview couldn’t start'), h('div.t-meta.pv-msg', null, st.message || ''), log,
        h('div.pv-actions', null,
          btn('pv-retry', 'Try again', () => this.reconnect({ start: true, install: st.needs_install }), '.primary'),
          btn('pv-fix', 'Ask ' + this.agentName() + ' to fix it', () => this.app.say('The live preview failed to start: ' + (st.message || '') + ' Look at `firedev preview status` and fix it.'))));
    } else if (s === 'native') {
      const n = st.native || {};
      el.append(icon('android'), h('div.t-label', null, (n.platform || 'Native') + ' app · no web preview'),
        h('div.t-meta.pv-msg', null, (n.why || '') + ' FireDev won’t pretend to run it in a web page.'),
        h('div.pv-actions', null, (st.alternatives || []).slice(0, 3).map((a, i) => btn('pv-alt' + i, a.label, () => this.alternative(a), i === 0 ? '.primary' : ''))));
    } else if (s === 'none') {
      el.append(h('div.t-label', null, 'No web page here yet'), h('div.t-meta.pv-msg', null, st.message || ''),
        h('div.t-meta', null, 'Try “Build me a modern landing page”.'));
    } else {
      el.append(h('span.spinner'), h('div.t-label', null, 'Looking for a web preview…'));
    }
  }

  alternative(a) {
    if (a.op === 'build') return this.app.say('Build the app');
    if (a.op === 'screenshots') return this.reconnect({ screenshots: true });
    if (a.op === 'agent') return this.app.say(a.text);
    if (a.op === 'start') return this.reconnect({ start: true, dir: a.dir });
    return null;
  }
}

function statusWord(s) {
  return { starting: 'Starting…', idle: 'Not running', failed: 'Couldn’t start', native: 'No web preview', none: 'No web page yet', detecting: 'Looking…' }[s] || 'Checking…';
}
