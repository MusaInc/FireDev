/**
 * Overlays above the current screen. Approval always wins: while anything waits for approval the approval sheet
 * is on top and takes the remote. The voice sheet shows what was heard and what FireDev understood; results
 * celebrate finished builds and deployments.
 */

import { native } from './native.js';
import { bytes, clear, duration, focusable, h, icon, plural } from './util.js';

const HOLD_MS = 2000;

const SUGGEST = {
  home: ['Open my Android project', 'What’s the status?'],
  idle: ['Why is the build failing?', 'Run the tests', 'Build the app', 'Show me what changed', 'Deploy the preview', 'Check the logs'],
  failed: ['Why is the build failing?', 'Fix it and run the tests', 'Show me what changed'],
  working: ['What are you doing?', 'Stop', 'Show activity'],
  changed: ['Run the tests', 'Build the app', 'Show me what changed', 'Undo that change'],
  built: ['Deploy the preview', 'Show me what changed', 'Check the logs'],
};

export class Overlays {
  constructor(app) {
    this.app = app;
    this.root = document.getElementById('overlays');
    this.stack = []; // { id, el, layer }
    this.voice = null;
    this.approval = null;
    this.voiceTimer = 0;
    this.pendingResult = null;
  }

  // --- stack helpers ---------------------------------------------------------------------------------------

  open(id, el, opts) {
    this.close(id, true);
    this.root.appendChild(el);
    const entry = { id, el, opts: opts || {} };
    this.stack.push(entry);
    this.restack();
    return entry;
  }

  close(id, instant) {
    const i = this.stack.findIndex((e) => e.id === id);
    if (i < 0) return;
    const e = this.stack[i];
    this.stack.splice(i, 1);
    this.app.focus.remove(id);
    if (e.opts.onClose) e.opts.onClose();
    if (instant) e.el.remove();
    else {
      e.el.classList.add('closing');
      setTimeout(() => e.el.remove(), 180);
    }
    this.restack();
  }

  isOpen(id) { return this.stack.some((e) => e.id === id); }

  /** Rebuilds the focus layers so the screen is at the bottom and the approval sheet (if any) on top. */
  restack() {
    const f = this.app.focus;
    for (const e of this.stack) f.remove(e.id);
    const ordered = this.stack.filter((e) => e.id !== 'approval').concat(this.stack.filter((e) => e.id === 'approval'));
    for (const e of ordered) {
      e.el.style.zIndex = e.id === 'approval' ? 50 : 10 + ordered.indexOf(e);
      if (e.opts.focus === false) continue;
      f.push(e.el, { id: e.id, initial: e.opts.initial || null, onBack: e.opts.onBack || (() => this.close(e.id)), onKey: e.opts.onKey });
    }
  }

  reattachBase() { /* screens are under #overlays; nothing to move */ }

  update() {
    if (this.approval) this.renderApprovalTick();
  }

  // --- approval --------------------------------------------------------------------------------------------

  syncApprovals() {
    const a = this.app.approvals[0];
    if (!a) {
      if (this.isOpen('approval')) this.close('approval');
      this.approval = null;
      return;
    }
    if (this.approval && this.approval.id === a.id) return;
    this.approval = a;
    this.close('voice', true);
    this.open('approval', this.renderApproval(a), {
      initial: 'reject',
      onBack: () => {}, // Back never dismisses an approval: choose Reject or Approve.
      onKey: (action, down) => this.holdKey(action, down),
    });
  }

  renderApproval(a) {
    const app = this.app;
    const p = app.projects[a.project];
    const strong = a.level >= 3;
    const link = a.kind === 'link';
    this.holding = null;
    const reject = focusable(h('button.btn', null, 'Reject'), 'reject', { select: () => this.decide(a, false) });
    let approve;
    if (strong) {
      approve = focusable(h('button.btn.danger.hold', null, h('span.fill'), h('span', { style: 'position:relative' }, link ? 'Hold OK to link' : 'Hold OK to approve')), 'approve', {});
    } else {
      approve = focusable(h('button.btn.primary', null, 'Approve'), 'approve', { select: () => this.decide(a, true) });
    }
    const who = link ? 'Alexa wants to link to FireDev. It should have just told you this code:'
      : a.source === 'agent' ? (a.agent || 'The agent') + ' wants to run:' : 'You asked FireDev to run:';
    this.approvalEl = h('div.overlay', null, h('div.sheet.approval' + (strong ? '.l3' : ''), null,
      h('div.eyebrow', null, icon('shield'), link ? 'Link a voice assistant' : strong ? 'Strong confirmation required' : 'Approval required',
        link ? null : h('span.tag.l' + a.level, { style: 'margin-left:auto' }, 'Level ' + a.level + ' · ' + a.level_name)),
      h('div.who', null, who),
      h('div.command', null, a.command),
      h('dl', null,
        h('dt', null, 'Action'), h('dd', null, a.title),
        h('dt', null, 'Reason'), h('dd', null, a.reason || '—'),
        h('dt', null, 'Risk'), h('dd', null, a.risk || '—'),
        p && !link ? h('dt', null, 'Where') : null, p && !link ? h('dd.muted', null, p.name + ' on ' + p.machine) : null),
      h('div.buttons', null, reject, approve),
      h('div.note', null, link ? 'Hold OK on the remote for two seconds to link. You can unlink it later with firedev unpair.'
        : strong ? 'This can’t be undone. Hold OK on the remote for two seconds to approve.' : 'Nothing runs until you choose. ◁ Back does not approve.')));
    return this.approvalEl;
  }

  holdKey(action, down) {
    const a = this.approval;
    if (!a || action !== 'select') return false;
    // OK from the phone remote approves only when held, at every level: a tap or a dropped phone never approves.
    const phone = this.app.input && this.app.input.source() === 'phone';
    if (a.level < 3 && !phone && !this.holding) return false;
    if (this.app.focus.focusedId() !== 'approve') return false;
    const btn = this.approvalEl.querySelector('[data-focus-id="approve"]');
    if (down) {
      if (this.holding) return true;
      if (phone && a.level < 3) this.app.toast('Keep holding OK on your phone to approve', 'warn');
      btn.classList.add('holding');
      this.holding = setTimeout(() => {
        this.holding = null;
        btn.classList.remove('holding');
        this.decide(a, true, true);
      }, HOLD_MS);
    } else if (this.holding) {
      clearTimeout(this.holding);
      this.holding = null;
      btn.classList.remove('holding');
      this.app.toast('Keep holding OK to approve', 'warn');
    }
    return true;
  }

  decide(a, approve, held) {
    this.app.command(approve ? 'approve_action' : 'reject_action', { approval: a.id, held: !!held }).then((r) => {
      if (r.ok) {
        this.app.approvals = this.app.approvals.filter((x) => x.id !== a.id);
        this.syncApprovals();
      }
    });
  }

  renderApprovalTick() { /* static sheet; kept for future countdowns */ }

  // --- voice -----------------------------------------------------------------------------------------------

  suggestions() {
    const p = this.app.project();
    if (!p || this.app.route.name === 'home') return SUGGEST.home;
    if (p.task) return SUGGEST.working;
    if (p.deploy && p.deploy.state === 'passed') return SUGGEST.built;
    if (p.build && p.build.state === 'failed') return SUGGEST.failed;
    if (p.build && p.build.state === 'passed') return SUGGEST.built;
    if (p.changes && p.changes.files.length) return SUGGEST.changed;
    return SUGGEST.idle;
  }

  ensureVoice(mode) {
    if (this.approval) return null;
    if (!this.voice || !this.isOpen('voice')) {
      const words = h('div.words');
      const state = h('div.state');
      const intent = h('div.intent');
      const chips = h('div.chips');
      const foot = h('div.voice-foot');
      const sheet = h('div.sheet.voice', null,
        h('div.voice-top', null, h('div.mic', null, h('span.ring'), h('span.ring.r2'), icon('mic')), h('div.voice-text', null, state, words, intent)),
        chips, foot);
      const el = h('div.overlay', { style: 'background:linear-gradient(to top, rgba(4,4,6,0.9), rgba(4,4,6,0.35) 70%)' }, sheet);
      this.voice = { el, sheet, words, state, intent, chips, foot, mode };
      this.open('voice', el, { focus: mode === 'menu', onClose: () => { this.voice = null; native.stopSpeech(); } });
    }
    return this.voice;
  }

  setVoiceState(kind, label) {
    const v = this.voice;
    v.sheet.classList.remove('listening', 'understood');
    if (kind) v.sheet.classList.add(kind);
    clear(v.state).append(label);
  }

  /** Opened from the remote (☰ or Speak): listen on the TV if it can, and offer quick commands. */
  openVoice() {
    if (this.approval) return;
    if (this.isOpen('voice') && this.voice.mode === 'menu') { this.close('voice'); return; }
    this.close('voice', true);
    const v = this.ensureVoice('menu');
    clear(v.words);
    clear(v.intent);
    if (native.speechAvailable()) {
      this.setVoiceState('listening', 'Listening…');
      native.startSpeech();
    } else {
      this.setVoiceState('', 'Say it to Alexa or your phone, or choose:');
      v.words.append(h('span.partial', null, '“' + this.suggestions()[0] + '”'));
    }
    clear(v.chips);
    this.suggestions().forEach((s, i) => v.chips.appendChild(focusable(h('div.chip', null, s), 'chip-' + i, { select: () => this.app.say(s, 'tv') })));
    clear(v.foot).append(h('span.t-hint', null, 'Phone microphone: Home → Phone microphone  ·  ◁ Close'));
    this.restack();
  }

  voiceEvent(ev) {
    const d = ev.data || {};
    if (this.approval && ev.type !== 'voice_reply') return;
    // In a spoken conversation on the TV the dock (or the screen's rail) shows this instead of the sheet.
    // Where to go is the director's job (director.js); this only shows what was heard and understood.
    if (this.app.voice.active && (!d.source || d.source === 'tv') && !this.isOpen('voice')) return;
    // Home is the voice screen: its orb and sentence show the conversation, no sheet on top.
    if (this.app.route.name === 'home' && !this.isOpen('voice')) return;
    clearTimeout(this.voiceTimer);
    if (ev.type === 'voice_listening') {
      if (!d.active) { if (this.voice && this.voice.mode !== 'menu') this.close('voice'); return; }
      const v = this.ensureVoice('remote');
      if (!v) return;
      this.setVoiceState('listening', 'Listening…' + (d.source && d.source !== 'tv' ? '  ·  ' + sourceName(d.source) : ''));
      clear(v.words);
      clear(v.intent);
      return;
    }
    if (ev.type === 'voice_partial') {
      const v = this.ensureVoice('remote');
      if (!v) return;
      this.setVoiceState('listening', 'Listening…' + (d.source && d.source !== 'tv' ? '  ·  ' + sourceName(d.source) : ''));
      clear(v.words).append(h('span.partial', null, '“' + d.text + '”'));
      return;
    }
    if (ev.type === 'voice_command') {
      const v = this.ensureVoice(this.voice ? this.voice.mode : 'remote');
      if (!v) return;
      clear(v.chips);
      this.setVoiceState('understood', 'Understood' + (d.source && d.source !== 'tv' ? '  ·  ' + sourceName(d.source) : ''));
      clear(v.words).append('“' + d.text + '”');
      const it = d.intent || {};
      clear(v.intent).append(icon('check', 'ok'), h('b', null, it.title || ''), it.name === 'send_agent_task' && d.agent ? h('span.pill', null, 'Agent: ' + d.agent) : null);
      if (this.voice.mode === 'menu') { this.voice.mode = 'remote'; this.restack(); }
      this.voiceTimer = setTimeout(() => this.close('voice'), 4200);
      return;
    }
    if (ev.type === 'voice_reply') {
      if (d.intent === 'approve_action') return;
      const v = this.voice;
      if (v && d.reply) {
        v.intent.appendChild(h('span.muted', { style: 'margin-left:12px' }, '— ' + d.reply));
        if (!d.ok) v.sheet.querySelector('.mic').style.background = 'var(--bad-dim)';
      } else if (d.reply && !d.ok) {
        this.app.toast(d.reply, 'bad');
      }
      this.voiceTimer = setTimeout(() => this.close('voice'), d.ok ? 3000 : 5000);
    }
  }

  focusApprove() {
    if (this.approval) {
      this.app.focus.focus('approve');
      this.app.toast(this.approval.level >= 3 ? 'Hold OK on the remote to approve' : 'Press OK on the remote to approve', 'warn');
    }
  }

  nativeSpeech(e) {
    const v = this.voice;
    if (!v) return;
    if (e.phase === 'partial') clear(v.words).append(h('span.partial', null, '“' + e.text + '”'));
    else if (e.phase === 'final' && e.text) this.app.say(e.text, 'tv');
    else if (e.phase === 'error' || e.phase === 'end') this.setVoiceState('', e.phase === 'error' ? 'Didn’t catch that. Choose one, or try again:' : 'Choose one:');
  }

  // --- results ---------------------------------------------------------------------------------------------

  resultEvent(ev) {
    const d = ev.data || {};
    // Results of the agent's own checks show on the workspace tiles; the big card is for what the user asked for.
    if (d.source === 'agent') return;
    clearTimeout(this.pendingResult);
    this.pendingResult = setTimeout(() => this.showResult(ev), 500);
  }

  showResult(ev) {
    if (this.approval) return;
    const d = ev.data;
    const p = this.app.project(ev.project);
    if (!p) return;
    const lines = [];
    let ok = true;
    let title;
    let lead;
    if (ev.type === 'tests_finished') {
      ok = d.state === 'passed';
      title = ok ? 'Tests passed' : 'Tests failed';
      lead = d.total !== null && d.total !== undefined ? d.passed + ' of ' + d.total + ' passed' : d.message || '';
      (d.failures || []).slice(0, 4).forEach((f) => lines.push([false, f.name, f.message]));
      if (ok && d.total) lines.push([true, 'Tests: ' + d.passed + ' / ' + d.total, duration(d.duration)]);
    } else if (ev.type === 'build_finished') {
      ok = d.state === 'passed';
      title = ok ? 'Build complete' : 'Build failed';
      if (ok) {
        const t = p.tests || {};
        if (t.total) lines.push([t.failed === 0, 'Tests: ' + t.passed + ' / ' + t.total, '']);
        lines.push([true, p.actions && p.actions.build ? p.actions.build.title.replace(/^Build the /, '').replace(/^./, (c) => c.toUpperCase()) : 'Build', '']);
        const art = (p.build && p.build.artifact) || d.artifact;
        if (art) lines.push([true, (/\.apk$/.test(art.name) ? 'APK generated' : 'Artifact ready'), art.name + ' · ' + bytes(art.size)]);
        lead = 'Finished in ' + duration(d.duration);
      } else {
        lead = d.error_count ? plural(d.error_count, 'error') + ' found' : 'See the activity for details';
        (d.errors || []).slice(0, 4).forEach((e) => lines.push([false, e.message, e.name ? e.name + ':' + e.line : '']));
      }
    } else {
      ok = d.state === 'passed';
      title = ok ? 'Deployment complete' : 'Deployment failed';
      lead = ok ? 'All checks passed' : d.message || '';
      const t = d.tests || p.tests || {};
      const b = d.build || p.build || {};
      if (ok) {
        if (t.total) lines.push([t.failed === 0, 'Tests: ' + t.passed + ' / ' + t.total, '']);
        if (b.state === 'passed') lines.push([true, 'Build verified', b.artifact ? b.artifact.name : '']);
        (d.checks || []).forEach((c) => lines.push([true, c.replace(/:.*$/, ''), c.indexOf(':') > 0 ? c.replace(/^[^:]*:\s*/, '') : '']));
        if (d.url) lines.push([true, 'Preview live', d.url.replace(/^https?:\/\//, '')]);
      }
    }
    const done = focusable(h('button.btn.primary', null, 'Done'), 'done', { select: () => this.close('result') });
    const el = h('div.overlay', null, h('div.sheet.result' + (ok ? '' : '.failed'), null,
      h('div.badge', null, icon(ok ? 'bigcheck' : 'bigcross', ok ? 'ok' : 'bad')),
      h('div.t-display', null, title),
      h('div.lead', null, lead),
      h('ul', null, lines.map((l, i) => h('li', { style: 'animation-delay:' + (180 + i * 140) + 'ms' }, icon(l[0] ? 'check' : 'cross', l[0] ? 'ok' : 'bad'), h('span.clip', null, l[1]), l[2] ? h('span.dim.clip', null, l[2]) : null))),
      h('div.foot', null, h('span.t-meta', null, p.name + '  ·  ' + p.machine), done)));
    this.close('voice', true);
    this.open('result', el, { initial: 'done' });
  }

  // --- details, output, text ---------------------------------------------------------------------------

  openDetail(kind, pid) {
    const p = this.app.project(pid);
    if (!p) return;
    const body = h('div', { style: 'overflow:hidden;flex:1;margin-top:32px' });
    let title;
    if (kind === 'build') {
      const b = p.build || {};
      title = b.state === 'failed' ? 'Build failed' : b.state === 'passed' ? 'Build passing' : b.state === 'running' ? 'Building…' : 'Not built yet';
      if (b.errors && b.errors.length) b.errors.slice(0, 6).forEach((e) => body.appendChild(h('div.err', null, h('div.loc', null, (e.file || '') + (e.line ? ':' + e.line + (e.col ? ':' + e.col : '') : '')), h('div.msg', null, e.message))));
      else if (b.artifact) body.appendChild(h('div.t-body.muted', null, b.artifact.path + '  ·  ' + bytes(b.artifact.size)));
      else body.appendChild(h('div.t-body.muted', null, 'Say “Build the app” to build it.'));
    } else {
      const t = p.tests || {};
      title = t.total ? t.passed + ' / ' + t.total + ' tests passed' : t.state === 'error' ? (t.message || 'Tests failed') : t.state === 'running' ? 'Running tests…' : 'Tests not run yet';
      if (t.failures && t.failures.length) t.failures.slice(0, 6).forEach((f) => body.appendChild(h('div.err', null, h('div.loc', null, f.name), h('div.msg', null, f.message))));
      else if (t.errors && t.errors.length) t.errors.slice(0, 6).forEach((e) => body.appendChild(h('div.err', null, h('div.loc', null, (e.file || '') + ':' + (e.line || '')), h('div.msg', null, e.message))));
      else body.appendChild(h('div.t-body.muted', null, t.total ? 'Every test passed.' : 'Say “Run the tests”.'));
    }
    const actionName = kind === 'build' ? 'build' : 'test';
    const runIt = p.actions && p.actions[actionName] ? focusable(h('button.btn.primary', null, kind === 'build' ? 'Build again' : 'Run tests'), 'run', {
      select: () => { this.close('detail'); this.app.command('run_action', { project: pid, action: actionName }); } }) : null;
    const el = h('div.overlay', null, h('div.sheet.detail', null,
      h('div.t-meta', null, kind === 'build' ? 'Build' : 'Tests'), h('div.t-title', { style: 'margin-top:10px' }, title), body,
      h('div', { style: 'display:flex;gap:24px;margin-top:36px' }, focusable(h('button.btn', null, 'Close'), 'close', { select: () => this.close('detail') }), runIt)));
    this.open('detail', el, { initial: 'close' });
  }

  openOutput(pid, ref, title) {
    const text = h('div.code', null, 'Loading…');
    const inner = h('div.output-body', null, text);
    let offset = 0;
    const pane = focusable(h('div.pane', { style: 'flex:1;margin-top:28px' }, inner), 'out', {
      up: () => scroll(-8), down: () => scroll(8), left: () => true, right: () => true, select: () => this.close('output'),
    });
    function scroll(n) {
      const max = Math.max(0, inner.scrollHeight - pane.clientHeight);
      offset = Math.max(-max, Math.min(0, offset - n * 40));
      inner.style.transform = 'translateY(' + offset + 'px)';
      return true;
    }
    const el = h('div.overlay', null, h('div.sheet.detail', { style: 'width:1720px;height:940px' },
      h('div.t-meta', null, 'Terminal output  ·  ▲▼ scroll  ·  ◁ close'), h('div.t-heading.mono.clip', { style: 'margin-top:12px;font-size:34px' }, title || ''), pane));
    this.open('output', el, { initial: 'out' });
    this.app.link.command('get_output', { project: pid, ref }).then((r) => {
      const out = (r.ok && r.result.output) || 'No output.';
      const lines = out.split('\n');
      clear(text).append(lines.slice(-600).join('\n'));
      requestAnimationFrame(() => scroll(100000));
    });
  }

  openText(title, body) {
    const el = h('div.overlay', null, h('div.sheet.detail', null,
      h('div.t-meta', null, 'Agent message'), h('div.t-body', { style: 'margin-top:20px;white-space:pre-wrap;overflow:hidden' }, body || title),
      h('div', { style: 'margin-top:36px' }, focusable(h('button.btn', null, 'Close'), 'close', { select: () => this.close('text') }))));
    this.open('text', el, { initial: 'close' });
  }

  openPhoneLink() {
    const app = this.app;
    const img = h('img', { alt: '' });
    const url = h('div.terminal', { style: 'font-size:26px;word-break:break-all;margin-top:24px' }, 'Creating a link…');
    const el = h('div.overlay', null, h('div.sheet.detail', { style: 'width:1400px' },
      h('div.t-meta', null, 'Phone microphone'),
      h('div.t-title', { style: 'margin-top:10px' }, 'Speak to FireDev from your phone'),
      h('div', { style: 'display:flex;gap:48px;margin-top:40px;align-items:center' },
        h('div.qr', null, img),
        h('div', { style: 'flex:1;min-width:0' },
          h('div.t-body.muted', null, 'Scan with your phone’s camera. Hold the button on the page and speak, or use its remote to move around. The link works for 12 hours and never approves by itself: approving from the phone takes OK held on Approve.'),
          url)),
      h('div', { style: 'margin-top:40px' }, focusable(h('button.btn', null, 'Done'), 'close', { select: () => this.close('phone') }))));
    this.open('phone', el, { initial: 'close' });
    const base = app.config.server.replace(/\/$/, '');
    app.link.command('voice_link', { base }).then((r) => {
      if (!r.ok) { clear(url).append(r.error || 'Couldn’t create a link'); return; }
      clear(url).append(r.result.url.replace(/#t=.*/, '#t=…'));
      img.src = base + '/api/qr.svg?token=' + encodeURIComponent(app.link.token || '') + '&data=' + encodeURIComponent(r.result.url);
    });
  }

  confirm(title, body, okLabel, onOk) {
    const el = h('div.overlay', null, h('div.sheet.detail', { style: 'width:1100px' },
      h('div.t-title', null, title),
      h('div.t-body.muted', { style: 'margin-top:20px' }, body),
      h('div', { style: 'display:flex;gap:24px;margin-top:44px' },
        focusable(h('button.btn', null, 'Cancel'), 'cancel', { select: () => this.close('confirm') }),
        focusable(h('button.btn.danger', null, okLabel), 'ok', { select: () => { this.close('confirm'); onOk(); } }))));
    this.open('confirm', el, { initial: 'cancel' });
  }

  /** A preview from a generated screen, full size. Only addresses the service allowed reach here. */
  openPreview(url) {
    const frame = h('iframe', { src: url, tabindex: '-1', sandbox: 'allow-scripts allow-same-origin', style: 'width:1600px;height:900px;border:0;border-radius:24px;background:#fff' });
    const el = h('div.overlay', null, h('div.sheet.detail', { style: 'width:1720px;height:1010px;padding:36px 60px' },
      h('div', { style: 'display:flex;align-items:center;gap:24px;margin-bottom:20px' }, h('div.t-meta.clip', { style: 'flex:1' }, url),
        focusable(h('button.btn.small', null, 'Close'), 'close', { select: () => this.close('preview') })), frame));
    this.open('preview', el, { initial: 'close' });
  }

  confirmExit() {
    if (!native.inShell) return;
    const el = h('div.overlay', null, h('div.sheet.detail', { style: 'width:1100px' },
      h('div.t-title', null, 'Leave FireDev?'),
      h('div.t-body.muted', { style: 'margin-top:20px' }, 'Your agents keep working on your machines. Open FireDev again to pick up where they are.'),
      h('div', { style: 'display:flex;gap:24px;margin-top:44px' },
        focusable(h('button.btn', null, 'Stay'), 'stay', { select: () => this.close('exit') }),
        focusable(h('button.btn.primary', null, 'Leave'), 'leave', { select: () => native.exit() }))));
    this.open('exit', el, { initial: 'stay' });
  }
}

function sourceName(s) {
  return { voice: 'from your phone', alexa: 'from Alexa', assistant: 'from your assistant', cli: 'from the terminal' }[s] || '';
}
