/**
 * Layered layout for diagrams (architecture, flowcharts, dependency graphs): nodes are ranked by their longest
 * path from a source, ordered within each rank to reduce crossings, and edges are drawn as SVG curves. Small
 * and deterministic, so the same graph always lands in the same place and a re-render doesn't jump.
 */

export const NODE_W = 280;
export const NODE_H = 104;
const GAP_RANK = 120;
const GAP_NODE = 36;

export function layoutGraph(nodes, edges, direction) {
  const ids = nodes.map((n) => n.id);
  const out = {};
  const inn = {};
  ids.forEach((id) => { out[id] = []; inn[id] = []; });
  (edges || []).forEach((e) => {
    if (out[e.from] && inn[e.to] && e.from !== e.to) { out[e.from].push(e.to); inn[e.to].push(e.from); }
  });
  // Break cycles first: a depth-first walk in the given order marks edges that point back up the current path,
  // and ranking (longest path from a source) ignores them.
  const back = {};
  const state = {};
  function dfs(id) {
    state[id] = 1;
    for (const t of out[id]) {
      if (state[t] === 1) back[id + '>' + t] = true;
      else if (!state[t]) dfs(t);
    }
    state[id] = 2;
  }
  ids.forEach((id) => { if (!state[id] && !inn[id].length) dfs(id); });
  ids.forEach((id) => { if (!state[id]) dfs(id); });
  const rank = {};
  function visit(id) {
    if (rank[id] !== undefined) return rank[id];
    rank[id] = 0;
    let r = 0;
    for (const p of inn[id]) if (!back[p + '>' + id]) r = Math.max(r, visit(p) + 1);
    rank[id] = r;
    return r;
  }
  ids.forEach(visit);
  const layers = [];
  ids.forEach((id) => { (layers[rank[id]] = layers[rank[id]] || []).push(id); });
  // Two barycentre sweeps to untangle edges.
  const pos = {};
  layers.forEach((l) => l.forEach((id, i) => { pos[id] = i; }));
  for (let sweep = 0; sweep < 2; sweep++) {
    for (let r = 1; r < layers.length; r++) {
      const ups = (id) => inn[id].filter((p) => !back[p + '>' + id]);
      const bc = (id) => (ups(id).length ? ups(id).reduce((a, p) => a + pos[p], 0) / ups(id).length : pos[id]);
      layers[r].sort((a, b) => bc(a) - bc(b));
      layers[r].forEach((id, i) => { pos[id] = i; });
    }
  }
  const across = direction === 'down';
  const maxCount = Math.max.apply(null, layers.map((l) => l.length));
  const span = maxCount * (across ? NODE_W : NODE_H) + (maxCount - 1) * GAP_NODE;
  const boxes = {};
  layers.forEach((l, r) => {
    const size = l.length * (across ? NODE_W : NODE_H) + (l.length - 1) * GAP_NODE;
    const off = (span - size) / 2;
    l.forEach((id, i) => {
      const along = r * ((across ? NODE_H : NODE_W) + GAP_RANK);
      const cross = off + i * ((across ? NODE_W : NODE_H) + GAP_NODE);
      boxes[id] = across ? { x: cross, y: along } : { x: along, y: cross };
    });
  });
  const width = across ? span : layers.length * NODE_W + (layers.length - 1) * GAP_RANK;
  const height = across ? layers.length * NODE_H + (layers.length - 1) * GAP_RANK : span;
  return { boxes, width: Math.max(width, NODE_W), height: Math.max(height, NODE_H), across };
}

export function edgePath(a, b, across) {
  if (across) {
    const x1 = a.x + NODE_W / 2; const y1 = a.y + NODE_H; const x2 = b.x + NODE_W / 2; const y2 = b.y;
    const my = (y1 + y2) / 2;
    return 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + my + ' ' + x2 + ',' + my + ' ' + x2 + ',' + y2;
  }
  const x1 = a.x + NODE_W; const y1 = a.y + NODE_H / 2; const x2 = b.x; const y2 = b.y + NODE_H / 2;
  const mx = (x1 + x2) / 2;
  return 'M' + x1 + ',' + y1 + ' C' + mx + ',' + y1 + ' ' + mx + ',' + y2 + ' ' + x2 + ',' + y2;
}
