/** Pieces shared by several screens: the top bar, connection pill and remote hints. */

import { h, icon, svg, icons } from '../util.js';

export function connState(app, p) {
  if (app.status !== 'online') return { cls: 'warn', text: app.status === 'offline' ? 'FireDev offline · retrying' : 'Connecting…', dot: 'warn' };
  if (!p) return { cls: 'ok', text: 'FireDev online', dot: 'ok' };
  const c = p.connection;
  if (c === 'connected') return { cls: 'ok', text: p.machine, dot: 'ok' };
  if (c === 'reconnecting') return { cls: 'warn', text: 'Reconnecting…', dot: 'warn pulse' };
  if (c === 'error') return { cls: 'bad', text: 'Not connected', dot: 'bad' };
  if (c === 'disconnected') return { cls: '', text: 'Not connected', dot: '' };
  return { cls: 'warn', text: p.connection_message || 'Connecting…', dot: 'live pulse' };
}

export function connPill(app, p) {
  const s = connState(app, p);
  return h('span.pill.conn' + (s.cls ? '.' + s.cls : ''), null, h('span.dot' + (s.dot ? '.' + s.dot.split(' ').join('.') : '')), s.text);
}

export function agentPill(p) {
  if (!p) return null;
  const a = p.agent || {};
  const state = p.connection !== 'connected' ? 'offline' : a.state;
  const label = { working: 'working', ready: 'ready', unavailable: 'not installed', offline: 'offline' }[state] || state;
  const dot = state === 'working' ? 'live pulse' : state === 'ready' ? 'ok' : state === 'unavailable' ? 'bad' : '';
  return h('span.pill', null, h('span.dot' + (dot ? '.' + dot.split(' ').join('.') : '')), (a.name || p.agent) + ' · ' + label);
}

export function brand() {
  return h('div.brand', null, svg(icons.mark), 'FireDev');
}

export function hints(list) {
  return h('div.hints', null, list.map(([key, text]) => h('span', null, h('span.key', null, key), text)));
}

export { icon };
