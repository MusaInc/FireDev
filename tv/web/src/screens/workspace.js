/**
 * Project workspace: a few large regions readable from the sofa.
 *   top    the task's stages (Understand → Explore → Build → Verify → Done)
 *   left   while the agent works, the live view: what it is doing now, what it just said, its last steps;
 *          afterwards the summary of what it did
 *   right  the files it is touching (live), tests and build
 *   bottom the latest command, in words, with its output's latest line
 */

import { bytes, clear, duration, focusable, h, icon, plural, summarize } from '../util.js';
import { agentPill, connPill, hints } from './common.js';
import { humanize, liveState, pastVerb } from '../live.js';
import { nowPanel, stageRail } from './livebits.js';

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
    this.rail = h('div.rail-wrap');
    this.el = h('section.screen.workspace', null,
      h('div.topbar', null, this.head, this.chips, this.stop, this.previewBtn, this.screenBtn, this.speak),
      this.rail,
      h('div.ws', null, this.taskCard, h('div.side', null, this.buildTile, this.testsTile, this.changesTile), this.cmd));
    this.sigs = {};
    this.seen = new Set(); // step ids already shown, so only new ones animate in
    this.clock = null; // { el, elapsed, at } for the running task's timer
    this.ticks = 0;
    this.timer = setInterval(() => this.tick(), 1000);
  }

  unmount() { clearInterval(this.timer); }

  tick() {
    // Every few seconds: the "no new step for …" line moves on even when no event arrives.
    if (++this.ticks % 5 === 0) this.update();
    const c = this.clock;
    if (c && c.el.isConnected) c.el.textContent = clockText(c.elapsed + (Date.now() - c.at) / 1000);
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
      clear(this.head).append(h('div.t-heading.clip', null, p.name), h('div.t-meta.clip', null, p.machine + '  ·  ' + shortPath(p.path)));
    }
    if (this.changed('chips', [app.status, p.connection, p.connection_message, p.agent && p.agent.state, p.machine])) {
      clear(this.chips).append(connPill(app, p), agentPill(p));
    }
    this.stop.style.display = p.task ? '' : 'none';
    this.screenBtn.style.display = app.ui.surfaces[this.pid] ? '' : 'none';
    const pv = app.preview.state(this.pid);
    this.previewBtn.style.display = pv && (pv.kind === 'web' || pv.kind === 'static' || pv.status === 'running') ? '' : 'none';
    const live = liveState(p);
    this.live = live;
    this.el.classList.toggle('is-live', !!(live && (live.running || live.waiting)));
    const b = p.build || {};
    // Only projects that have been built show the build tile; the rest of the column goes to tests and files.
    this.buildTile.style.display = !b.state || b.state === 'unknown' ? 'none' : '';
    this.renderRail(live);
    this.renderTask(p, live);
    this.renderBuild(p);
    this.renderTests(p);
    this.renderChanges(p, live);
    this.renderCommand(p);
  }

  renderTask(p, live) {
    const t = p.task || p.last_task;
    const steps = t ? (p.activity || []).filter((a) => a.task === t.id && a.kind !== 'task' && a.kind !== 'task-end' && a.kind !== 'agent').slice(-6) : [];
    const running = t && (t.status === 'running' || t.status === 'queued');
    const waiting = t && t.status === 'waiting_approval';
    if (running || waiting) {
      // The live view keeps its frame and updates its parts, so only what changed moves.
      if (this.changed('task', ['live', t.id, p.connection])) this.buildLive(t);
      this.renderLive(p, live);
      return;
    }
    this.clock = null;
    const lastMsg = t ? (p.activity || []).filter((a) => a.task === t.id && a.kind === 'agent').slice(-1)[0] : null;
    const key = [t && t.id, t && t.status, t && t.status_text, t && t.summary, steps.map((s) => [s.id, s.status, s.title]), lastMsg && lastMsg.id, p.connection, p.queue,
      live && live.files.map((f) => f.path)];
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
    const label = running || waiting ? 'Current task' : t.status === 'completed' ? 'Last task · done in ' + duration(t.elapsed) : t.status === 'cancelled' ? 'Last task · stopped' : 'Last task · failed';
    const agentName = t.kind === 'agent' ? p.agent.name : 'FireDev';
    c.append(h('div.label', null,
      h('span.dot' + (running ? '.live.pulse' : waiting ? '.warn' : t.status === 'completed' ? '.ok' : '.bad')),
      h('span.t-meta', null, label + '  ·  ' + agentName)),
    h('div.t-title.title.clamp2', null, t.title));
    const sum = summarize(t.summary || (t.status === 'failed' ? t.error : '') || '');
    const ok = t.status === 'completed';
    const box = h('div.summary', null, h('div.headline.clamp3', null, sum.headline || (ok ? 'Done.' : t.error || 'Stopped.')));
    if (sum.bullets.length) box.appendChild(h('ul', null, sum.bullets.slice(0, 3).map((b) => h('li.clamp2', null, b))));
    const files = live ? live.files : [];
    c.classList.toggle('is-done', ok);
    if (ok) c.prepend(h('div.done-badge' + (Date.now() / 1000 - (t.finished || 0) < 20 ? '.fresh' : ''), null, h('span.done-burst'), icon('bigcheck')));
    // A receipt for the work: how long, how many files, how the tests went.
    const tests = p.tests || {};
    const stats = [[duration(t.elapsed), 'Time']];
    if (files.length) stats.push([String(files.length), files.length === 1 ? 'File' : 'Files']);
    if (tests.total !== null && tests.total !== undefined && tests.state !== 'unknown') stats.push([tests.passed + '/' + tests.total, 'Tests', tests.failed ? 'bad' : 'ok']);
    c.append(box, h('div', { style: 'flex:1' }),
      ok && (files.length || stats.length > 1) ? h('div.receipt', null, stats.map(([v, k, tone]) => h('div.stat', null, h('div.stat-v' + (tone ? '.' + tone : ''), null, v), h('div.stat-k', null, k))))
        : h('div.steps', { style: 'flex:none' }, steps.slice(-2).map((s) => stepRow(s, false))));
  }

  /** The live view's frame for one running task. */
  buildLive(t) {
    const c = clear(this.taskCard);
    c.classList.remove('is-done');
    this.seen = new Set();
    const timer = h('span.timer');
    this.clock = { el: timer, elapsed: t.elapsed || 0, at: Date.now() };
    this.lv = {
      label: h('div.label'),
      title: h('div.live-title.clamp2'),
      now: h('div.now-slot'),
      said: h('div.said'),
      feed: h('div.feed'),
      next: h('div.t-meta.up-next'),
      timer,
    };
    const v = this.lv;
    c.append(v.label, v.title, v.now, v.said, v.feed, v.next);
    this.sigs.lv = {};
  }

  renderLive(p, live) {
    const v = this.lv;
    const t = live.task;
    const sig = this.sigs.lv;
    const set = (k, val) => { const s = JSON.stringify(val); if (sig[k] === s) return false; sig[k] = s; return true; };
    this.clock.elapsed = t.elapsed || 0;
    this.clock.at = Date.now();
    this.tick();
    if (set('label', [live.waiting, p.agent.name])) {
      clear(v.label).append(h('span.dot' + (live.waiting ? '.warn' : '.live.pulse')),
        h('span.t-meta', null, 'Current task  ·  ' + (t.kind === 'agent' ? p.agent.name : 'FireDev')), h('span.grow'),
        h('span.timer-pill', null, icon('clock'), v.timer));
    }
    if (set('title', t.title)) clear(v.title).append(t.title);
    const quiet = live.quiet >= 15 ? live.quiet - (live.quiet % 5) : 0;
    if (set('now', [live.now, quiet])) clear(v.now).append(nowPanel(live.now, false, quiet));
    const said = live.said || (live.waiting ? 'Waiting for your approval' : t.status_text);
    if (set('said', said)) {
      clear(v.said).append(live.said ? h('span.quote', null, said) : h('span.dim', null, said || ''));
      v.said.classList.remove('said-in');
      void v.said.offsetWidth;
      v.said.classList.add('said-in');
    }
    // The steps before the current one: newest at the bottom, older ones fading out.
    const past = live.steps.slice(-3);
    if (set('feed', past.map((s) => [s.id, s.status, s.past]))) {
      clear(v.feed);
      past.forEach((s, i) => {
        const fresh = !this.seen.has(s.id);
        this.seen.add(s.id);
        const failed = s.status === 'failed' && !s.soft; // a search that finds nothing hasn't failed
        const glyph = s.status === 'running' ? h('span.spinner') : icon(failed ? 'cross' : s.icon || 'check', failed ? 'bad' : s.tone || '');
        v.feed.appendChild(h('div.feed-row' + (fresh ? '.enter' : '') + (i === past.length - 1 ? '.latest' : ''), { style: 'opacity:' + (0.45 + 0.55 * ((i + 1) / past.length)).toFixed(2) },
          h('span.feed-glyph', null, glyph), h('span.clip', null, s.past || s.object),
          s.file ? h('span.tag.l1', null, s.verb === 'Creating' ? 'New' : 'Edit') : null));
      });
    }
    if (set('next', p.queue)) v.next.textContent = p.queue && p.queue.length ? 'Up next: ' + p.queue.join(', ') : '';
  }

  renderRail(live) {
    const key = live ? [live.task.id, live.stage, live.running || live.waiting, live.task.status] : null;
    if (!this.changed('rail', key)) return;
    clear(this.rail);
    if (live) this.rail.appendChild(stageRail(live.stage, live.running || live.waiting, live.task.status === 'failed' || live.task.status === 'cancelled'));
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
    if (!this.changed('tests', [t.state, t.passed, t.total, t.failed, t.message, this.el.classList.contains('is-live')])) return;
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
      sub = h('div.sub', null, this.el.classList.contains('is-live') ? 'They run once the work is in' : 'Say “Run the tests”');
    }
    el.append(h('div.head', null, h('span.t-meta', null, 'Tests'), h('span.t-meta', null, t.duration ? duration(t.duration) : '')), big, sub);
  }

  renderChanges(p, live) {
    const c = p.changes || { files: [] };
    if (live && (live.running || live.waiting)) return this.renderLiveFiles(c, live);
    if (!this.changed('changes', [c.files.map((f) => [f.path, f.added, f.removed])])) return;
    const el = clear(this.changesTile);
    el.classList.remove('files-live');
    const n = c.files.length;
    el.append(h('div.head', null, h('span.t-meta', null, 'Changes'), h('span.t-meta', null, n ? 'OK to review' : '')));
    if (!n) {
      el.append(h('div.big.muted', null, 'No changes'), h('div.sub', null, 'Working tree is clean'));
      return;
    }
    el.append(h('div.big', null, plural(n, 'file'), h('span.ok', { style: 'font-size:36px' }, '+' + c.added), h('span.bad', { style: 'font-size:36px' }, '−' + c.removed)),
      h('div.files', null, c.files.slice(0, 4).map((f) => h('div.file-row.compact', null,
        h('span.file-name', null, h('b.clip', null, f.name)),
        h('span.file-delta', null, h('span.ok', null, '+' + f.added), h('span.bad', null, '−' + f.removed))))),
      n > 4 ? h('div.t-meta', null, '+ ' + plural(n - 4, 'more file')) : null);
  }

  /** While the agent works: the files it touched in this task, newest first, the newest one lit up. */
  renderLiveFiles(c, live) {
    const files = live.files;
    if (!this.changed('changes', ['live', files.map((f) => [f.path, f.change, f.added, f.removed])])) return;
    const el = clear(this.changesTile);
    el.classList.add('files-live');
    el.append(h('div.head', null, h('span.t-meta', null, 'Files'), h('span.t-meta', null, files.length ? plural(files.length, 'file') : '')));
    if (!files.length) {
      el.append(h('div.files-empty', null, h('span.scan'), h('span', null, 'No files touched yet')));
      return;
    }
    const now = Date.now() / 1000;
    el.append(h('div.files', null, files.slice(0, 5).map((f, i) => h('div.file-row' + (i === 0 && now - f.ts < 10 ? '.fresh' : '') + '.' + f.change, null,
      h('span.file-badge', null, icon(f.change === 'new' ? 'sparkle' : f.change === 'deleted' ? 'cross' : 'pencil')),
      h('span.file-name', null, h('b.clip', null, f.name), f.dir ? h('span.file-dir.clip', null, f.dir) : null),
      f.added !== null ? h('span.file-delta', null, h('span.ok', null, '+' + f.added), h('span.bad', null, '−' + f.removed))
        : h('span.file-delta.dim', null, f.change === 'new' ? 'new' : '')))),
      files.length > 5 ? h('div.t-meta', null, '+ ' + plural(files.length - 5, 'more file')) : null);
  }

  renderCommand(p) {
    const lc = p.last_command;
    if (!this.changed('cmd', lc ? [lc.command, lc.running, lc.exit_code, lc.latest, lc.level] : null)) return;
    const el = clear(this.cmd);
    if (!lc) {
      el.append(h('span.prompt.mono', null, '$'), h('span.cmd.mono.dim', null, 'No commands yet'), hints([['☰', 'Speak'], ['◁', 'Projects']]));
      return;
    }
    const says = humanize(lc.command);
    const nothing = says.soft && lc.exit_code === 1; // grep/rg: no matches
    const state = lc.running ? h('span.spinner') : icon(lc.exit_code === 0 || nothing ? 'check' : 'cross', lc.exit_code === 0 ? 'ok' : nothing ? '' : 'bad');
    el.classList.toggle('running', !!lc.running);
    el.append(h('span.cmd-glyph', null, icon(says.icon)),
      h('span.cmd-words', null, h('span.cmd-say.clip', null, (lc.running ? says.verb : pastVerb(says.verb)) + ' ' + says.object),
        h('span.cmd.mono.clip', null, h('span.prompt', null, lc.source === 'agent' ? (this.p.agent.name || 'agent') + ' $ ' : '$ '), lc.command)),
      lc.level >= 1 ? h('span.tag.l' + lc.level, null, lc.level_name) : null,
      h('span.latest.mono.clip', null, lc.running ? (lc.latest || '') : lc.exit_code === 0 ? (lc.latest || 'Done') : nothing ? 'No matches' : 'Exit ' + lc.exit_code + (lc.latest ? ' · ' + lc.latest : ''),
        lc.running ? h('i.caret') : null),
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

function shortPath(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts.length > 2 ? '…/' + parts.slice(-2).join('/') : path;
}

function clockText(sec) {
  const s = Math.max(0, Math.floor(sec));
  return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
}
