/**
 * Reading the agent's work as a story instead of a log: which stage it is in, what it is doing right now (in words,
 * with the file or command as the object), which files it has touched and what each command means. Pure functions of
 * the project snapshot, shared by the workspace's live view and the preview's side panel.
 */

export const STAGES = [
  { id: 'understand', label: 'Understand' },
  { id: 'explore', label: 'Explore' },
  { id: 'build', label: 'Build' },
  { id: 'verify', label: 'Verify' },
  { id: 'done', label: 'Done' },
];
const RANK = { understand: 0, explore: 1, build: 2, verify: 3, done: 4 };

const base = (p) => String(p || '').replace(/\/+$/, '').split('/').pop();

/** A shell command in plain words: { verb, object, icon, stage }. */
export function humanize(command) {
  const c = String(command || '').trim().replace(/^(sudo|env \S+=\S+|cd \S+ &&)\s+/g, '');
  const words = c.split(/\s+/);
  const first = base(words[0]);
  // (A heredoc marker such as `python3 - <<'PY'` is not a file name.)
  const arg = words.slice(1).map((w) => w.replace(/^<<-?|^['"]|['"]$/g, '').replace(/^['"]|['"]$/g, '')).filter((w) => w && !w.startsWith('-') && /^[\w.\/~@-]+$/.test(w) && !/^[A-Z]{1,5}$/.test(w)).pop();
  if (first === 'firedev') {
    const sub = words[1] || '';
    if (sub === 'say') return { verb: 'Talking', object: 'you through it', icon: 'spark', stage: null, soft: true };
    if (sub === 'preview') return { verb: 'Opening', object: 'the live preview', icon: 'globe', stage: 'verify', soft: true };
    if (sub === 'ui') return { verb: 'Designing', object: 'a screen', icon: 'agent', stage: 'build', soft: true };
    if (sub === 'run') return humanize(words.slice(2).join(' ') === 'test' ? './test.sh' : words.slice(2).join(' ') || 'build');
    return { verb: 'Checking', object: 'with FireDev', icon: 'spark', stage: null, soft: true };
  }
  if (/(^|\/)test\.sh$|\b(pytest|jest|vitest|mocha|phpunit|rspec)\b|\b(npm|pnpm|yarn|bun) (run )?test\b|\bgo test\b|\bcargo test\b|gradlew? \S*test/i.test(c)) {
    return { verb: 'Running', object: 'the tests', icon: 'flask', stage: 'verify' };
  }
  if (/\b(eslint|ruff|flake8|tsc|mypy|lint)\b/.test(c)) return { verb: 'Checking', object: 'the code', icon: 'shield', stage: 'verify' };
  if (/(^|\/)build\.sh$|\b(npm|pnpm|yarn|bun) run build\b|\b(vite|next|webpack) build\b|\bcargo build\b|\bmake\b|gradlew? \S*(assemble|build)/i.test(c)) {
    return { verb: 'Building', object: 'the app', icon: 'build', stage: 'verify' };
  }
  if (/(^|\/)deploy\.sh$|\bdeploy\b/.test(c)) return { verb: 'Deploying', object: 'the preview', icon: 'rocket', stage: 'verify' };
  if (/\b(npm|pnpm|yarn|bun) (i|install|add)\b|\bpip3? install\b|\bpoetry (add|install)\b|\bcargo add\b/.test(c)) {
    return { verb: 'Installing', object: 'packages', icon: 'box', stage: 'build' };
  }
  if (/^(rg|grep|ag|ack)$/.test(first)) {
    const term = (c.match(/["']([^"']{2,40})["']/) || [])[1] || words.slice(1).find((w) => !w.startsWith('-'));
    return { verb: 'Searching', object: term ? 'for “' + term + '”' : 'the code', icon: 'search', stage: 'explore', soft: true };
  }
  if (first === 'find' || first === 'ls' || first === 'tree' || first === 'fd') return { verb: 'Looking around', object: arg && arg !== '.' ? base(arg) : 'the project', icon: 'folder', stage: 'explore' };
  if (/^(cat|head|tail|less|bat|nl|wc)$/.test(first) || (first === 'sed' && /\s-n\s/.test(c))) {
    return { verb: 'Reading', object: arg ? base(arg) : 'a file', icon: 'file', stage: 'explore' };
  }
  if (first === 'git') {
    const sub = words[1] || '';
    if (/^(status|diff|log|show|blame)$/.test(sub)) return { verb: 'Checking', object: 'what changed', icon: 'branch', stage: 'explore' };
    return { verb: 'Saving', object: 'with git', icon: 'branch', stage: 'build' };
  }
  if (/^(mkdir|touch|cp|mv|rm|apply_patch|patch|tee)$/.test(first) || first === 'sed') return { verb: 'Editing', object: arg ? base(arg) : 'files', icon: 'pencil', stage: 'build' };
  if (/^(curl|wget|http)$/.test(first)) return { verb: 'Checking', object: 'a web address', icon: 'globe', stage: 'verify' };
  if (/^(node|python3?|deno|bun|ruby|go)$/.test(first)) return { verb: 'Running', object: arg ? base(arg) : 'a ' + first.replace(/3$/, '') + ' script', icon: 'terminal', stage: 'verify' };
  return { verb: 'Running', object: first || 'a command', icon: 'terminal', stage: 'explore' };
}

const PAST = { Running: 'Ran', Searching: 'Searched', Reading: 'Read', Building: 'Built', Installing: 'Installed', Checking: 'Checked',
  'Looking around': 'Looked around', Editing: 'Edited', Saving: 'Saved', Deploying: 'Deployed', Creating: 'Created', Testing: 'Tested' };
/** "Running" → "Ran", for things that have finished. */
export function pastVerb(verb) { return PAST[verb] || verb; }

/** One activity item as { verb, object, icon, stage, tone }. */
export function describe(item) {
  if (!item) return null;
  switch (item.kind) {
    case 'file': {
      const [done, ...rest] = String(item.title || '').split(' ');
      const verb = { Created: 'Creating', Deleted: 'Deleting' }[done] || 'Editing';
      return { verb, past: item.title, object: rest.join(' ') || base(item.detail), where: dirOf(item.detail), icon: verb === 'Creating' ? 'sparkle' : verb === 'Deleting' ? 'cross' : 'pencil', stage: 'build', file: true };
    }
    case 'command': return { ...humanize(item.detail || item.title), past: item.title };
    case 'tests': return { verb: 'Testing', object: item.title.replace(/^Tests:\s*/, ''), past: item.title, icon: 'flask', stage: 'verify', tone: item.status === 'failed' ? 'bad' : 'ok' };
    case 'build': return { verb: 'Building', object: 'the app', past: item.title, icon: 'build', stage: 'verify', tone: item.status === 'failed' ? 'bad' : 'ok' };
    case 'deploy': return { verb: 'Deploying', object: 'the preview', past: item.title, icon: 'rocket', stage: 'verify' };
    case 'preview': return { verb: 'Opening', object: 'the live preview', past: item.title, icon: 'globe', stage: 'verify' };
    case 'approval': return { verb: 'Waiting', object: 'for your OK', past: item.title, icon: 'shield', stage: null, tone: 'warn' };
    case 'screen': return { verb: 'Designing', object: 'a screen', past: item.title, icon: 'agent', stage: 'build' };
    case 'undo': return { verb: 'Undoing', object: 'changes', past: item.title, icon: 'cross', stage: 'build' };
    default: return { verb: '', object: item.title, past: item.title, icon: 'dot', stage: null };
  }
}

/** The agent's latest reasoning headline ("Designing midnight theme") as the step between steps. */
function thoughtNow(t) {
  const th = t.thought;
  if (!th || !th.text || Date.now() / 1000 - (th.ts || 0) > 120) return null;
  const [first, ...rest] = th.text.split(' ');
  if (/ing$/i.test(first) && rest.length) return { verb: first, object: rest.join(' '), icon: 'spark', thinking: true, thought: true };
  return { verb: 'Working on', object: th.text.charAt(0).toLowerCase() + th.text.slice(1), icon: 'spark', thinking: true, thought: true };
}

function dirOf(path) {
  const parts = String(path || '').split('/');
  return parts.length > 1 ? parts.slice(0, -1).join('/') + '/' : '';
}

/** Everything the live view shows for the current (or last) task. */
export function liveState(p) {
  const t = p && (p.task || p.last_task);
  if (!t) return null;
  const items = (p.activity || []).filter((a) => a.task === t.id && a.kind !== 'task' && a.kind !== 'task-end' && a.kind !== 'agent' && a.kind !== 'notice');
  const running = t.status === 'running' || t.status === 'queued';
  const waiting = t.status === 'waiting_approval';
  let reached = 0;
  for (const a of items) {
    const d = describe(a);
    if (d && d.stage) reached = Math.max(reached, RANK[d.stage]);
  }
  if (!running && !waiting && t.status === 'completed') reached = RANK.done;
  const latest = items[items.length - 1];
  let now = latest ? describe(latest) : null;
  // A finished step that isn't a command still running is history: between steps the agent is thinking.
  if (running && latest && latest.status !== 'running' && latest.kind !== 'file' && Date.now() / 1000 - (latest.ts || 0) > 6) now = null;
  if (waiting) now = { verb: 'Waiting', object: 'for your OK', icon: 'shield', tone: 'warn' };
  if (running && !now) now = thoughtNow(t) || { verb: 'Thinking', object: items.length ? 'about the next step' : 'about your request', icon: 'spark', thinking: true };
  // Files: the newest change per path, joined with the diff's line counts.
  const byPath = {};
  const changes = {};
  ((p.changes && p.changes.files) || []).forEach((f) => { changes[f.path] = f; });
  items.filter((a) => a.kind === 'file').forEach((a) => {
    const path = a.detail || a.title;
    const d = describe(a);
    byPath[path] = { id: a.id, path, name: base(path), dir: dirOf(path), change: d.verb === 'Creating' ? 'new' : d.verb === 'Deleting' ? 'deleted' : 'edited', ts: a.ts };
  });
  const files = Object.values(byPath).sort((a, b) => b.ts - a.ts).map((f) => {
    const c = changes[f.path] || Object.values(changes).find((x) => x.path && (x.path.endsWith('/' + f.path) || f.path.endsWith('/' + x.path)));
    return { ...f, added: c ? c.added : null, removed: c ? c.removed : null };
  });
  const said = (p.conversation || []).filter((c) => c.role === 'agent' && c.task === t.id).slice(-1)[0];
  // How long nothing visible has happened (a step, a file, a headline): the truthful "still working" line.
  const lastTs = Math.max(latest ? latest.ts || 0 : 0, t.thought ? t.thought.ts || 0 : 0, t.started || 0);
  return {
    task: t, running, waiting, items, files, now, said: said ? said.text : '',
    quiet: running && lastTs ? Math.max(0, Math.round(Date.now() / 1000 - lastTs)) : 0,
    stage: reached, stages: STAGES,
    // The step on the "now" panel isn't repeated in the list under it.
    steps: items.slice(-6, now && latest && now.object === describe(latest).object ? -1 : undefined).slice(-5)
      .map((a) => ({ id: a.id, status: a.status, ...describe(a) })),
  };
}
