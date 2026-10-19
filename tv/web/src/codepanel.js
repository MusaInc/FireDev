/**
 * Live code: the third column of the live view. Replays the real code the agent just wrote, region by region, typed
 * at many times human speed with a cursor, auto-indentation, syntax highlighting and smooth scrolling; switches
 * files as changes arrive.
 *
 * Honesty rules: only lines from `code_change` events (read back from the project's files) are ever shown or typed.
 * The header always says it is a replay of a finished edit and how long ago it happened. When nothing is being
 * replayed it says what the agent is really doing (thinking, running something, done); the cursor never types then.
 *
 * Performance (old Fire TV WebViews): one requestAnimationFrame loop; finished lines are highlighted once, only the
 * line being typed is re-rendered each frame; at most ~70 lines exist in the DOM; scrolling is a transform.
 */

import { clear, h, icon } from './util.js';
import { highlighter, esc } from './highlight.js';

const MAX_ROWS = 70;
const BACKLOG = 4; // changes waiting beyond this are skipped (the newest ones are what matter)
const CPS_TARGET = 950; // characters per second (a fast human types ~8)

function ago(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  return s < 3 ? 'just now' : s < 60 ? s + 's ago' : Math.round(s / 60) + ' min ago';
}

function base(p) { return String(p || '').split('/').pop(); }

export class CodePanel {
  constructor(agentName) {
    this.agentName = agentName || 'the agent';
    this.queue = [];
    this.playing = null;
    this.files = []; // recent paths, newest first
    this.raf = 0;
    this.idle = { text: 'Code appears here as ' + this.agentName + ' writes it', kind: 'wait' };
    this.tabs = h('div.cp-tabs');
    this.status = h('div.cp-status');
    this.rows = h('div.cp-rows');
    this.view = h('div.cp-view', null, this.rows);
    this.empty = h('div.cp-empty', null, icon('code'), h('div', null, 'Live code'), h('div.cp-empty-sub', null, 'Every change ' + this.agentName + ' makes is replayed here, line by line.'));
    this.el = h('div.card.code-panel', null, h('div.cp-head', null, h('div.cp-title', null, h('span.cp-dot'), 'Live code'), this.tabs), this.status, h('div.cp-body', null, this.view, this.empty));
    this.renderStatus();
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    clearTimeout(this.nextTimer);
    clearInterval(this.agoTimer);
    this.gone = true;
  }

  /** A code_change event's data, with when it happened. */
  enqueue(change, ts) {
    if (!change || this.gone) return;
    this.queue.push({ ...change, ts: ts || Date.now() });
    while (this.queue.length > BACKLOG) this.queue.shift();
    if (!this.playing) this.next();
  }

  /** What the agent is really doing right now, for when nothing is being replayed. */
  setAgent(live, agentName) {
    if (agentName) this.agentName = agentName;
    let idle;
    if (!live) idle = this.files.length ? { text: 'All changes shown', kind: 'done' } : { text: 'Code appears here as ' + this.agentName + ' writes it', kind: 'wait' };
    else if (live.waiting) idle = { text: this.agentName + ' is waiting for your OK', kind: 'wait' };
    else if (live.running && live.now && live.now.thinking) idle = { text: this.agentName + ' is thinking…', kind: 'think' };
    else if (live.running && live.now) idle = { text: this.agentName + ': ' + live.now.verb.toLowerCase() + ' ' + live.now.object, kind: 'busy' };
    else if (live.running) idle = { text: this.agentName + ' is working…', kind: 'think' };
    else idle = { text: this.files.length ? 'All changes shown · ' + this.agentName + ' is done' : 'No code changes in this task', kind: 'done' };
    if (JSON.stringify(idle) === JSON.stringify(this.idle)) return;
    this.idle = idle;
    if (!this.playing) this.renderStatus();
  }

  renderStatus() {
    const s = clear(this.status);
    const p = this.playing;
    this.el.classList.toggle('is-typing', !!p);
    if (p) {
      s.className = 'cp-status replay';
      s.append(h('span.cp-chip', null, 'Replay'), h('span.clip', null, (p.known ? this.agentName + '’s edit to ' : 'Opened ') + base(p.path)),
        h('span.cp-ago', null, ago(p.ts)), p.known ? h('span.cp-delta', null, h('b.ok', null, '+' + (p.added || 0)), h('b.bad', null, '−' + (p.removed || 0))) : null);
      return;
    }
    s.className = 'cp-status ' + this.idle.kind;
    s.append(h('span.cp-state'), h('span.clip', null, this.idle.text));
  }

  renderTabs(active) {
    clear(this.tabs);
    this.files.slice(0, 4).forEach((f) => this.tabs.appendChild(h('span.cp-tab' + (f === active ? '.on' : ''), null, base(f))));
  }

  // --- playback ----------------------------------------------------------------------------------------------

  next() {
    clearTimeout(this.nextTimer);
    const c = this.queue.shift();
    if (!c) { this.playing = null; this.renderStatus(); this.setCaretIdle(); return; }
    this.playing = c;
    this.files = [c.path].concat(this.files.filter((f) => f !== c.path)).slice(0, 8);
    this.renderTabs(c.path);
    this.renderStatus();
    clearInterval(this.agoTimer);
    this.agoTimer = setInterval(() => { if (this.playing) { const a = this.status.querySelector('.cp-ago'); if (a) a.textContent = ago(this.playing.ts); } }, 1000);
    this.empty.style.display = 'none';
    const switching = this.shownPath !== c.path;
    this.shownPath = c.path;
    if (switching) { this.view.classList.remove('cp-switch'); void this.view.offsetWidth; this.view.classList.add('cp-switch'); }
    this.regions = (c.regions || []).slice();
    this.hl = highlighter(c.lang);
    this.playRegion(this.regions.shift());
  }

  playRegion(r) {
    if (!r) { this.finish(); return; }
    clear(this.rows);
    this.rows.style.transform = 'translateY(0)';
    this.hl.reset();
    let no = r.at - r.before.length + 1;
    r.before.forEach((t) => this.rows.appendChild(this.row(no++, this.hl.line(t), 'ctx')));
    this.removedRows = r.removed.slice(0, 12).map((t) => { const el = this.row('', esc(t), 'del'); this.rows.appendChild(el); return el; });
    this.firstNo = no;
    this.tail = h('div.cp-tail');
    this.rows.appendChild(this.tail);
    r.after.forEach((t, i) => this.tail.appendChild(this.row(no + r.added.length + r.more + i, this.hl.peek(t), 'ctx')));
    this.lines = r.added;
    this.more = r.more;
    this.li = 0;
    this.ci = 0;
    this.lineNo = no;
    this.cur = null;
    const chars = r.added.reduce((n, t) => n + t.length + 1, 0);
    const backlog = this.queue.length;
    // Fast enough to keep up with the agent, slow enough to read: a large file still takes a few seconds.
    const secs = Math.min(5.5, Math.max(0.6, chars / CPS_TARGET)) / (backlog >= 2 ? 2 : 1);
    this.cps = Math.max(60, chars / secs);
    this.scrollTo(this.removedRows.length ? this.removedRows[0] : this.tail);
    // Removed lines show for a moment, then fold away before the new ones are typed.
    const go = () => { this.removedRows.forEach((el) => el.remove()); this.last = 0; this.tick(); };
    if (this.removedRows.length) { this.removedRows.forEach((el) => el.classList.add('folding')); this.nextTimer = setTimeout(go, 520); }
    else go();
  }

  row(no, html, cls) {
    return h('div.cp-row' + (cls ? '.' + cls : ''), { html: '<span class="cp-no">' + no + '</span><span class="cp-tx">' + html + '</span>' });
  }

  tick() {
    this.raf = requestAnimationFrame((t) => {
      if (this.gone) return;
      const dt = this.last ? Math.min(0.1, (t - this.last) / 1000) : 1 / 60;
      this.last = t;
      // A little unevenness, like real typing bursts.
      let budget = this.cps * dt * (0.75 + Math.random() * 0.5) + (this.carry || 0);
      while (budget >= 1 && this.li < this.lines.length) {
        if (!this.cur) {
          this.cur = this.row(this.lineNo, '', 'typed');
          this.rows.insertBefore(this.cur, this.tail);
          this.curText = this.cur.lastChild;
          // Auto-indent: the editor puts the indentation in at once.
          this.ci = (this.lines[this.li].match(/^\s*/) || [''])[0].length;
          this.trim();
        }
        const line = this.lines[this.li];
        const take = Math.min(line.length - this.ci, Math.floor(budget));
        this.ci += take;
        budget -= take;
        if (this.ci >= line.length) {
          this.curText.innerHTML = this.hl.line(line);
          budget -= 1;
          this.li += 1;
          this.lineNo += 1;
          this.cur.classList.remove('typing');
          this.cur = null;
          if (budget < 1) break;
        } else break;
      }
      this.carry = Math.max(0, Math.min(1, budget));
      if (this.cur) {
        const line = this.lines[this.li];
        this.curText.innerHTML = this.hl.peek(line.slice(0, this.ci)) + '<b class="cp-caret"></b>';
        this.cur.classList.add('typing');
        this.scrollTo(this.cur);
      } else if (this.li < this.lines.length) {
        this.scrollTo(this.tail);
      }
      if (this.li >= this.lines.length) { this.doneRegion(); return; }
      this.tick();
    });
  }

  doneRegion() {
    if (this.more) this.rows.insertBefore(h('div.cp-row.more', null, h('span.cp-no'), h('span.cp-tx', null, '… ' + this.more + ' more lines')), this.tail);
    const last = this.rows.lastChild && this.tail.previousSibling;
    if (last && last.lastChild) last.lastChild.insertAdjacentHTML('beforeend', '<b class="cp-caret"></b>');
    this.scrollTo(last || this.tail);
    this.nextTimer = setTimeout(() => this.playRegion(this.regions.shift()), this.regions.length ? 650 : 0);
  }

  finish() {
    this.el.classList.remove('is-typing');
    this.nextTimer = setTimeout(() => this.next(), this.queue.length ? 500 : 900);
  }

  /** No replay running: the caret stops typing and only blinks, under the status that says what's really going on. */
  setCaretIdle() {
    this.el.classList.remove('is-typing');
  }

  trim() {
    while (this.rows.childNodes.length > MAX_ROWS) this.rows.removeChild(this.rows.firstChild);
  }

  scrollTo(el) {
    if (!el) return;
    const viewH = this.view.clientHeight || 600;
    const y = Math.max(0, el.offsetTop - viewH * 0.55);
    this.rows.style.transform = 'translateY(' + -Math.round(y) + 'px)';
  }
}
