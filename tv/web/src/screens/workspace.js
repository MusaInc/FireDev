/**
 * Project workspace: a few large regions readable from the sofa.
 *   left   current task: title, live status, the agent's latest steps, or its summary when done
 *   right  build, tests and changed files
 *   bottom the latest command and its result
 */

import { bytes, clear, duration, focusable, h, icon, plural, summarize } from '../util.js';
import { agentPill, connPill, hints } from './common.js';

const STEP_ICONS = { done: 'check', failed: 'cross', running: null, waiting: 'wait' };

function stepRow(item, current) {
  let glyph;
  if (item.status === 'running') glyph = h('span.spinner');
  else glyph = icon(STEP_ICONS[item.status] || 'dot', item.status === 'failed' ? 'bad' : item.status === 'done' ? 'ok' : item.status === 'waiting' ? 'warn' : '');
  const tag = item.kind === 'approval' ? h('span.tag.l' + (item.level || 2), null, item.level >= 3 ? 'Destructive' : 'Approval')
    : item.kind === 'file' ? h('span.tag.l1', null, 'Edit') : null;
  return h('div.step' + (current ? '.current' : ''), null, glyph, h('span.clip', null, item.title), tag);
}

export class WorkspaceScreen {
  constructor(app, route) {
    this.app = app;
    this.pid = route.project;
    this.head = h('div.grow');
    this.chips = h('div', { style: 'display:flex;gap:20px;align-items:center' });
    this.speak = focusable(h('button.btn.small', null, icon('mic'), 'Speak', h('span.key', null, '☰')), 'speak', { select: () => app.overlays.openVoice() });
    this.stop = focusable(h('button.btn.small.danger', null, 'Stop'), 'stop', { select: () => app.command('cancel_task', { project: this.pid }) });
    this.screenBtn = focusable(h('button.btn.small', null, icon('agent'), 'Screen'), 'screen', { select: () => app.go('surface', this.pid) });
    this.previewBtn = focusable(h('button.btn.small', null, icon('code'), 'Live preview'), 'preview', { select: () => app.preview.open(this.pid, { start: true }) });
    this.taskCard = focusable(h('div.card.task-card'), 'task', { select: () => app.navigate('activity', { project: this.pid }) });
    this.buildTile = focusable(h('div.tile'), 'build', { select: () => app.overlays.openDetail('build', this.pid) });
    this.testsTile = focusable(h('div.tile'), 'tests', { select: () => app.overlays.openDetail('tests', this.pid) });
    this.changesTile = focusable(h('div.tile'), 'changes', { select: () => app.navigate('diff', { project: this.pid }) });
    this.cmd = focusable(h('div.cmd-strip'), 'command', { select: () => this.openLastOutput() });
    this.el = h('section.screen.workspace', null,
      h('div.topbar', null, this.head, this.chips, this.stop, this.previewBtn, this.screenBtn, this.speak),
      h('div.ws', null, this.taskCard, h('div.side', null, this.buildTile, this.testsTile, this.changesTile), this.cmd));
    this.sigs = {};
  }

  initialFocus() { return 'task'; }

  get p() { return this.app.project(this.pid); }

  changed(key, value) {
    const s = JSON.stringify(value);
    if (this.sigs[key] === s) return false;
    this.sigs[key] = s;
    return true;
  }

  update() {
    const p = this.p;
    const app = this.app;
    if (!p) return;
    if (this.changed('head', [p.name, p.machine, p.path, p.tmux])) {
      clear(this.head).append(h('div.t-heading.clip', null, p.name), h('div.t-meta.clip', null, p.machine + '  ·  ' + p.path + '  ·  tmux ' + p.tmux));
    }
    if (this.changed('chips', [app.status, p.connection, p.connection_message, p.agent && p.agent.state, p.machine])) {
      clear(this.chips).append(connPill(app, p), agentPill(p));
    }
    this.stop.style.display = p.task ? '' : 'none';
    this.screenBtn.style.display = app.ui.surfaces[this.pid] ? '' : 'none';
    const pv = app.preview.state(this.pid);
    this.previewBtn.style.display = pv && (pv.kind === 'web' || pv.kind === 'static' || pv.status === 'running') ? '' : 'none';
    this.renderTask(p);
    this.renderBuild(p);
    this.renderTests(p);
    this.renderChanges(p);
    this.renderCommand(p);
  }

  renderTask(p) {
    const t = p.task || p.last_task;
    const steps = t ? (p.activity || []).filter((a) => a.task === t.id && a.kind !== 'task' && a.kind !== 'task-end' && a.kind !== 'agent').slice(-6) : [];
    const lastMsg = t ? (p.activity || []).filter((a) => a.task === t.id && a.kind === 'agent').slice(-1)[0] : null;
    const key = [t && t.id, t && t.status, t && t.status_text, t && t.summary, steps.map((s) => [s.id, s.status, s.title]), lastMsg && lastMsg.id, p.connection, p.queue];
    if (!this.changed('task', key)) return;
    const c = clear(this.taskCard);
    if (p.connection !== 'connected' && !t) {
      c.append(h('div.empty-task', null,
        h('div.t-meta', null, p.connection === 'error' ? 'Connection failed' : 'Opening project'),
        h('div.say', null, p.connection === 'error' ? p.connection_message : (p.connection_message || 'Connecting…')),
        p.connection === 'error' ? h('div.t-body.muted', null, 'Press OK on Speak and say “open my project” to try again.') : h('div.bar.indeterminate', { style: 'width:420px' }, h('i'))));
      return;
    }
    if (!t) {
      c.append(h('div.empty-task', null,
        h('div.t-meta', null, 'Ready · ' + (p.agent.name || 'Agent') + ' is listening'),
        h('div.say', null, 'What should ' + (p.agent.name || 'the agent') + ' do?'),
        h('div.t-body.muted', null, 'Try “Why is the build failing?” or “Run the tests”.')));
      return;
    }
    const running = t.status === 'running' || t.status === 'queued';
    const waiting = t.status === 'waiting_approval';
    const label = running || waiting ? 'Current task' : t.status === 'completed' ? 'Last task · done in ' + duration(t.elapsed) : t.status === 'cancelled' ? 'Last task · stopped' : 'Last task · failed';
    const agentName = t.kind === 'agent' ? p.agent.name : 'FireDev';
    c.append(h('div.label', null,
      h('span.dot' + (running ? '.live.pulse' : waiting ? '.warn' : t.status === 'completed' ? '.ok' : '.bad')),
      h('span.t-meta', null, label + '  ·  ' + agentName)),
    h('div.t-title.title.clamp2', null, t.title));
    if (running || waiting) {
      c.append(h('div.task-status', null, waiting ? icon('shield', 'warn') : h('span.spinner'), h('span.clip', null, waiting ? 'Waiting for your approval' : t.status_text)));
      c.append(h('div.steps', null, steps.map((s, i) => stepRow(s, i === steps.length - 1))));
      if (p.queue && p.queue.length) c.append(h('div.t-meta', { style: 'margin-top:16px' }, 'Up next: ' + p.queue.join(', ')));
      return;
    }
    const sum = summarize(t.summary || (t.status === 'failed' ? t.error : '') || '');
    const box = h('div.summary', null, h('div.headline.clamp3', null, sum.headline || (t.status === 'completed' ? 'Done.' : t.error || 'Stopped.')));
    if (sum.bullets.length) box.appendChild(h('ul', null, sum.bullets.map((b) => h('li.clamp2', null, b))));
    c.append(box, h('div', { style: 'flex:1' }), h('div.steps', { style: 'flex:none' }, steps.slice(-2).map((s) => stepRow(s, false))));
  }

  renderBuild(p) {
    const b = p.build || {};
    const deploy = p.deploy || {};
    if (!this.changed('build', [b.state, b.error_count, b.errors && b.errors[0], b.artifact, b.duration, deploy.state])) return;
    const el = clear(this.buildTile);
    el.className = el.className.replace(/\b(ok|bad|live)-state\b/g, '').trim();
    let big;
    let sub;
    if (b.state === 'running') {
      el.classList.add('live-state');
      big = h('div.big.live', null, h('span.spinner'), 'Building…');
      sub = h('div.bar.indeterminate', null, h('i'));
    } else if (b.state === 'failed') {
      el.classList.add('bad-state');
      const n = b.error_count || 0;
      big = h('div.big.bad', null, icon('cross'), n ? 'Failed · ' + plural(n, 'error') : 'Failed');
      const e = b.errors && b.errors[0];
      sub = h('div.sub.clip', null, e ? (e.name ? e.name + ':' + e.line + '  ' : '') + e.message : 'See the activity for details');
    } else if (b.state === 'passed') {
      el.classList.add('ok-state');
      big = h('div.big.ok', null, icon('check'), deploy.state === 'passed' ? 'Built · deployed' : 'Build passing');
      sub = h('div.sub.clip', null, b.artifact ? b.artifact.name + '  ·  ' + bytes(b.artifact.size) + (b.duration ? '  ·  ' + duration(b.duration) : '') : 'Finished in ' + duration(b.duration));
    } else {
      big = h('div.big.muted', null, 'Not built yet');
      sub = h('div.sub', null, 'Say “Build the app”');
    }
    el.append(h('div.head', null, h('span.t-meta', null, 'Build'), h('span.t-meta', null, 'OK for details')), big, sub);
  }

  renderTests(p) {
    const t = p.tests || {};
    if (!this.changed('tests', [t.state, t.passed, t.total, t.failed, t.message])) return;
    const el = clear(this.testsTile);
    el.className = el.className.replace(/\b(ok|bad|live)-state\b/g, '').trim();
    let big;
    let sub;
    if (t.state === 'running') {
      el.classList.add('live-state');
      big = h('div.big.live', null, h('span.spinner'), 'Running tests…');
      sub = h('div.bar.indeterminate', null, h('i'));
    } else if (t.total !== null && t.total !== undefined && t.state !== 'unknown') {
      const ok = t.failed === 0;
      el.classList.add(ok ? 'ok-state' : 'bad-state');
      big = h('div.big' + (ok ? '.ok' : '.bad'), null, icon(ok ? 'check' : 'cross'), t.passed + ' / ' + t.total + ' passed');
      sub = h('div.bar' + (ok ? '' : '.bad'), null, h('i', { style: 'width:' + (t.total ? Math.round((t.passed / t.total) * 100) : 0) + '%' }));
    } else if (t.state === 'error' || t.state === 'failed') {
      el.classList.add('bad-state');
      big = h('div.big.bad', null, icon('cross'), 'Tests failed');
      sub = h('div.sub.clip', null, t.message || '');
    } else if (t.state === 'passed') {
      el.classList.add('ok-state');
      big = h('div.big.ok', null, icon('check'), 'Tests passed');
      sub = h('div.sub', null, '');
    } else {
      big = h('div.big.muted', null, 'Not run yet');
      sub = h('div.sub', null, 'Say “Run the tests”');
    }
    el.append(h('div.head', null, h('span.t-meta', null, 'Tests'), h('span.t-meta', null, t.duration ? duration(t.duration) : '')), big, sub);
  }

  renderChanges(p) {
    const c = p.changes || { files: [] };
    if (!this.changed('changes', [c.files.map((f) => [f.path, f.added, f.removed])])) return;
    const el = clear(this.changesTile);
    const n = c.files.length;
    el.append(h('div.head', null, h('span.t-meta', null, 'Changes'), h('span.t-meta', null, n ? 'OK to review' : '')));
    if (!n) {
      el.append(h('div.big.muted', null, 'No changes'), h('div.sub', null, 'Working tree is clean'));
      return;
    }
    el.append(h('div.big', null, plural(n, 'file'), h('span.ok', { style: 'font-size:36px' }, '+' + c.added), h('span.bad', { style: 'font-size:36px' }, '−' + c.removed)),
      h('div.sub.clip', null, c.files.map((f) => f.name).join(', ')));
  }

  renderCommand(p) {
    const lc = p.last_command;
    if (!this.changed('cmd', lc ? [lc.command, lc.running, lc.exit_code, lc.latest, lc.level] : null)) return;
    const el = clear(this.cmd);
    if (!lc) {
      el.append(h('span.prompt.mono', null, '$'), h('span.cmd.mono.dim', null, 'No commands yet'), hints([['☰', 'Speak'], ['◁', 'Projects']]));
      return;
    }
    const state = lc.running ? h('span.spinner') : icon(lc.exit_code === 0 ? 'check' : 'cross', lc.exit_code === 0 ? 'ok' : 'bad');
    el.append(h('span.prompt.mono', null, lc.source === 'agent' ? (this.p.agent.name || 'agent') + ' $' : '$'),
      h('span.cmd.mono.clip', null, lc.command),
      lc.level >= 1 ? h('span.tag.l' + lc.level, null, lc.level_name) : null,
      h('span.latest.clip', null, lc.running ? lc.latest || '' : lc.exit_code === 0 ? (lc.latest || 'Done') : 'Exit ' + lc.exit_code + (lc.latest ? ' · ' + lc.latest : '')),
      state);
  }

  openLastOutput() {
    const lc = this.p && this.p.last_command;
    const item = (this.p.activity || []).filter((a) => a.has_output).slice(-1)[0];
    if (!lc && !item) return;
    this.app.overlays.openOutput(this.pid, lc && lc.run ? lc.run : item && item.ref, lc ? lc.command : item.title);
  }

  back() { this.app.navigate('home', { preselect: this.pid }); }
}
