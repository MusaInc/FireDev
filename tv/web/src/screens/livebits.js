/** Pieces of the live view shared by the workspace and the preview's side panel. */

import { h, icon } from '../util.js';
import { STAGES } from '../live.js';

/** Understand → Explore → Build → Verify → Done, filled up to the stage reached; the current one glows. */
export function stageRail(reached, live, failed) {
  const pct = (reached / (STAGES.length - 1)) * 100;
  return h('div.rail' + (live ? '.is-live' : '') + (failed ? '.is-failed' : ''), null,
    h('div.rail-track', null, h('i.rail-fill', { style: 'width:' + pct + '%' })),
    h('div.rail-nodes', null, STAGES.map((s, i) => h('div.rail-node' + (i < reached || (!live && i === reached && !failed) ? '.done' : i === reached ? '.current' : ''), null,
      h('span.rail-dot', null, i < reached || (!live && i === reached && !failed) ? icon('check') : null), h('span.rail-label', null, s.label)))));
}

/** The "now working on" panel: what the agent is doing, in words, with the file or thing it's doing it to. */
export function nowPanel(now, compact) {
  if (!now) return h('div.now.idle');
  return h('div.now' + (compact ? '.compact' : '') + (now.thinking ? '.thinking' : '') + (now.tone ? '.tone-' + now.tone : ''), null,
    h('div.now-glyph', null, h('span.now-ring'), icon(now.icon || 'spark')),
    h('div.now-text', null,
      h('div.now-verb', null, now.verb),
      h('div.now-object.clip', null, now.object),
      now.where ? h('div.now-where.mono.clip', null, now.where) : null),
    h('i.now-sweep'));
}
