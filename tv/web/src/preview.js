/**
 * Live preview controller: the TV's side of FireDev's live previews.
 *
 * The page itself runs on the preview gateway, a separate origin, inside a sandboxed frame. FireDev gets a one-time
 * address for it from the service (preview_open), keeps the remote, and talks to the page only through postMessage
 * with the small helper the gateway adds to it: keys while you use the page, reload, back and forward, scroll, click.
 * The helper reports the page, its title and errors; FireDev passes those on so the agent knows what you're looking at.
 */

import { focusable, h } from './util.js';

const PREFS = 'firedev.preview.';
export const SPLITS = [0.5, 0.6, 0.68, 0.76];
export const ZOOMS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
export const VIEWPORTS = {
  desktop: { w: 1280, h: 800, label: 'Desktop' },
  tablet: { w: 820, h: 1180, label: 'Tablet', device: true },
  mobile: { w: 390, h: 844, label: 'Mobile', device: true },
};

function load(pid) {
  try { return JSON.parse(window.localStorage.getItem(PREFS + pid) || '{}') || {}; } catch (e) { return {}; }
}
function save(pid, prefs) {
  try { window.localStorage.setItem(PREFS + pid, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ }
}

export function createPreview(app) {
  const ctl = {
    states: {}, // pid -> preview snapshot from the service
    prefs(pid) {
      const p = Object.assign({ layout: 'split', viewport: 'desktop', zoom: 1, split: 2 }, load(pid));
      if (ZOOMS.indexOf(p.zoom) < 0) p.zoom = 1;
      if (!(p.split >= 0 && p.split < SPLITS.length)) p.split = 2;
      if (!VIEWPORTS[p.viewport]) p.viewport = 'desktop';
      return p;
    },
    setPrefs(pid, change) {
      const p = Object.assign(ctl.prefs(pid), change);
      save(pid, p);
      return p;
    },
    state(pid) { return ctl.states[pid] || null; },
    screen() { return app.route.name === 'preview' && app.screen && app.screen.isPreview ? app.screen : null; },

    /** Opens the preview screen for a project. opts: {layout, start, path, screenshots} */
    open(pid, opts) {
      pid = pid || app.route.project || app.focusProject;
      if (!pid) return;
      opts = opts || {};
      if (opts.layout) ctl.setPrefs(pid, { layout: opts.layout });
      const cur = ctl.screen();
      if (cur && cur.pid === pid) {
        cur.applyPrefs();
        if (opts.path) cur.post({ op: 'go', path: opts.path });
        if (opts.screenshots && !cur.screenshots) cur.reconnect({ screenshots: true });
        else if (opts.start) cur.reconnect({ start: true });
        return;
      }
      app.focusProject = pid;
      app.navigate('preview', { project: pid, start: !!opts.start, path: opts.path || null, screenshots: !!opts.screenshots });
    },

    onState(pid, st) {
      ctl.states[pid] = st;
      const cur = ctl.screen();
      if (cur && cur.pid === pid) cur.onState(st);
    },

    onEvent(ev) {
      const d = ev.data || {};
      const pid = ev.project;
      const here = app.route.project || app.focusProject;
      if (ev.type === 'preview_state') return ctl.onState(pid, d.preview);
      if (ev.type === 'preview_reload') {
        const cur = ctl.screen();
        if (cur && cur.pid === pid) cur.reloadFrame(d.reason);
        return null;
      }
      if (ev.type === 'preview_control') {
        if (d.op === 'show' && !app.overlays.approval && pid === here) ctl.open(pid, { layout: d.layout || null, path: d.path || null });
        return null;
      }
      if (ev.type === 'preview_offer') {
        if (d.preview) ctl.states[pid] = d.preview;
        const cur = ctl.screen();
        // Live build: the page goes on screen beside the agent's work as soon as it exists, and reloads on every save.
        if (d.live && !(cur && cur.pid === pid) && !app.overlays.approval && app.route.name !== 'connect' && (pid === here || !app.route.project)) {
          return ctl.open(pid, { layout: 'split' });
        }
        if ((cur && cur.pid === pid) || pid !== here || app.overlays.approval) return null;
        if (d.auto && (app.route.name === 'workspace' || app.route.name === 'surface' || app.route.name === 'preview')) {
          return ctl.open(pid, { start: true });
        }
        return ctl.offer(pid, d.message);
      }
      return null;
    },

    /** A card that offers the preview after the agent changed something visual. It never steals the remote. */
    offer(pid, message) {
      const ov = app.overlays;
      const show = focusable(h('button.btn.small.primary', null, 'Show the preview'), 'pv-show', { select: () => { ov.close('pv-offer'); ctl.open(pid, { start: true }); } });
      const later = focusable(h('button.btn.small', null, 'Not now'), 'pv-later', { select: () => ov.close('pv-offer') });
      const el = h('div.pv-offer', null, h('div.pv-offer-body', null, h('div.t-label', null, 'Live preview'), h('div.t-meta', null, message || 'There is something new to see.')),
        h('div.pv-offer-buttons', null, show, later));
      ov.open('pv-offer', el, { initial: 'pv-show' });
      clearTimeout(ctl.offerTimer);
      ctl.offerTimer = setTimeout(() => ov.close('pv-offer'), 25000);
    },

    /** Voice: {type: 'preview', op, layout, viewport, zoom, size, nav, dir, text, path, screenshots} */
    clientAction(a, pid) {
      pid = pid || app.route.project || app.focusProject;
      const cur = ctl.screen();
      if (a.op === 'show') return ctl.open(pid, { layout: a.layout || null, screenshots: !!a.screenshots });
      if (a.op === 'close') return cur ? cur.close() : null;
      if (a.op === 'state') return cur ? cur.update() : null;
      if (!cur) {
        // A control said while the preview isn't on screen: show it first, then apply.
        ctl.open(pid, {});
        setTimeout(() => { const c = ctl.screen(); if (c) c.control(a); }, 50);
        return null;
      }
      return cur.control(a);
    },
  };

  // The page's messages. Only the frame FireDev opened, from the gateway's origin, is listened to.
  window.addEventListener('message', (e) => {
    const cur = ctl.screen();
    if (cur) cur.onMessage(e);
  });
  return ctl;
}
