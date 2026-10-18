/**
 * The TV's own check of a generated screen before rendering it. The service has already validated it strictly;
 * this is the second line: a screen that is malformed, too big or too deep falls back to a plain message instead
 * of a broken or blank TV.
 */

export const SCHEMA = 'firedev.ui/1';
const KNOWN = ['stack', 'grid', 'tabs', 'tab', 'section', 'card', 'text', 'badge', 'button', 'metric', 'progress', 'timeline', 'list',
  'table', 'chart', 'graph', 'code', 'diff', 'terminal', 'image', 'preview', 'choice', 'form', 'agent_status', 'approvals'];
const MAX_NODES = 300;
const MAX_DEPTH = 10;

export function checkSurface(s) {
  if (!s || typeof s !== 'object') return 'No screen';
  if (s.schema !== SCHEMA) return 'Unsupported screen version ' + s.schema;
  let count = 0;
  const ids = {};
  function visit(n, depth) {
    if (!n || typeof n !== 'object' || Array.isArray(n)) return 'A component is malformed';
    if (++count > MAX_NODES) return 'Too many components';
    if (depth > MAX_DEPTH) return 'Nested too deeply';
    if (KNOWN.indexOf(n.type) < 0) return 'Unknown component ' + n.type;
    if (typeof n.id !== 'string' || ids[n.id]) return 'Component ids are missing or repeated';
    ids[n.id] = 1;
    const kids = n.children || [];
    if (!Array.isArray(kids)) return 'Children must be a list';
    for (const c of kids) {
      const err = visit(c, depth + 1);
      if (err) return err;
    }
    return null;
  }
  return visit(s.root, 1);
}
