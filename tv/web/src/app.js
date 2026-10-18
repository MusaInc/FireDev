/**
 * FireDev TV app: connects to the FireDev service, keeps the latest project snapshots, routes between screens
 * and raises overlays (approvals, voice, results) from structured events. Screens render from snapshots, so a
 * reconnect or a missed event can never leave the TV showing stale state.
 */

import { createFocus } from './focus.js';
import { createInput } from './input.js';
import { createLink } from './net.js';
import { native } from './native.js';
import { clear, h } from './util.js';
import { HomeScreen } from './screens/home.js';
import { WorkspaceScreen } from './screens/workspace.js';
import { ActivityScreen } from './screens/activity.js';
import { DiffScreen } from './screens/diff.js';
import { CenterScreen } from './screens/center.js';
import { SurfaceScreen } from './screens/surface.js';
import { Overlays } from './overlays.js';
import { createVoice } from './voice.js';

const SCREENS = { home: HomeScreen, workspace: WorkspaceScreen, activity: ActivityScreen, diff: DiffScreen, center: CenterScreen, surface: SurfaceScreen };

export function createApp() {
  const stage = document.getElementById('stage');
  const screenEl = document.getElementById('screen');
  const toastEl = document.getElementById('toast');

  const app = {
    status: 'connecting',
    everOnline: false,
    pairingCode: null,
    pairingExpiresAt: 0,
    pairingExpired: false,
    client: null,
    home: { projects: [] },
    projects: {},
    approvals: [],
    focusProject: null,
    route: { name: 'center' },
    screen: null,
    config: native.config(),
    // Generated screens per project ({surface, version, patch}) and the live data they bind to.
    ui: { surfaces: {}, data: { agents: [], agents_updated: 0, history: {} } },
  };

  // --- stage scaling: everything is laid out on 1920×1080 -----------------------------------------------
  // Scale to what is actually visible. Some TV WebViews report a layout viewport larger than the screen
  // (innerWidth 1920 while 960 CSS px show), so take the smallest non-zero measurement.
  function visibleSize() {
    const vv = window.visualViewport;
    const de = document.documentElement;
    const pick = (vals) => Math.min.apply(null, vals.filter((v) => v > 0));
    return [pick([window.innerWidth, de.clientWidth, vv ? vv.width : 0]), pick([window.innerHeight, de.clientHeight, vv ? vv.height : 0])];
  }
  function fit() {
    const [w, h] = visibleSize();
    if (!isFinite(w) || !isFinite(h)) return;
    const s = Math.min(w / 1920, h / 1080);
    stage.style.transform = 'scale(' + s + ')';
    stage.style.left = Math.round((w - 1920 * s) / 2) + 'px';
    stage.style.top = Math.round((h - 1080 * s) / 2) + 'px';
  }
  window.addEventListener('resize', fit);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', fit);
  window.addEventListener('load', fit);
  fit();
  // Older WebViews can report 0×0 until the first layout; settle once more after it.
  setTimeout(fit, 300);

  app.focus = createFocus();
  app.overlays = new Overlays(app);
  app.voice = createVoice(app);

  // --- rendering -----------------------------------------------------------------------------------------
  let frame = 0;
  app.render = function () {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (app.screen) app.screen.update();
      app.overlays.update();
      app.voice.renderDock();
      app.focus.restore();
    });
  };

  app.navigate = function (name, params) {
    if (app.screen && app.screen.unmount) app.screen.unmount();
    app.route = Object.assign({ name }, params || {});
    clear(screenEl);
    const Screen = SCREENS[name];
    app.screen = new Screen(app, app.route);
    screenEl.appendChild(app.screen.el);
    app.overlays.reattachBase();
    app.screen.update();
    app.focus.reset(app.screen.el, { id: 'screen', initial: app.screen.initialFocus ? app.screen.initialFocus() : null,
      onBack: () => app.screen.back(), onKey: (a, d) => (app.screen.key ? app.screen.key(a, d) : false), onFocus: (el) => app.screen.onFocus && app.screen.onFocus(el) });
    app.overlays.restack();
  };

  app.project = function (pid) {
    return app.projects[pid || app.route.project || app.focusProject] || null;
  };

  let toastTimer = 0;
  app.toast = function (text, kind) {
    clear(toastEl);
    if (kind) toastEl.appendChild(h('span.dot.' + kind));
    toastEl.appendChild(document.createTextNode(text));
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3600);
  };

  app.command = function (name, args) {
    return app.link.command(name, args).then((r) => {
      if (!r.ok && (r.error || (r.result && r.result.message))) app.toast(r.error || r.result.message, 'bad');
      return r;
    });
  };

  app.openProject = function (pid) {
    app.focusProject = pid;
    app.navigate('workspace', { project: pid });
    const p = app.projects[pid];
    if (!p || p.connection !== 'connected') app.command('open_project', { project: pid });
  };

  /** What the remote is pointing at on a generated screen, so "make that bigger" knows what "that" is. */
  app.uiContext = function () {
    if (app.route.name !== 'surface') return null;
    const el = app.focus.focusedEl();
    const holder = el && el.closest ? el.closest('[data-gid]') : null;
    const pid = app.route.project;
    const entry = app.ui.surfaces[pid];
    return { focused: holder ? holder.dataset.gid : null, version: entry ? entry.version : null };
  };

  app.say = function (text, source) {
    app.link.voice(text, { source: source || 'tv', project: app.route.project || app.focusProject, ui: app.uiContext() });
  };

  /** Navigation targets used by voice replies and generated screens. */
  app.go = function (to, pid) {
    pid = pid || app.route.project || app.focusProject;
    if (to === 'home') return app.navigate('home', { preselect: pid });
    if (!pid) return null;
    if (to === 'surface') return app.route.name === 'surface' && app.route.project === pid ? null : app.navigate('surface', { project: pid });
    if (to === 'activity') return app.navigate('activity', { project: pid });
    if (to === 'changes') return app.navigate('diff', { project: pid });
    if (to === 'back') return app.focus.back();
    if (to === 'workspace' || to === 'terminal') {
      if (app.route.name !== 'workspace' || app.route.project !== pid) app.navigate('workspace', { project: pid });
      if (to === 'terminal' && app.screen && app.screen.openLastOutput) app.screen.openLastOutput();
    }
    return null;
  };

  function surfaceArrived(pid, fromVoice) {
    const here = app.route.project || app.focusProject;
    if (pid !== here || app.overlays.approval) return;
    // A new screen takes over the workspace and its own screen; it never pulls you out of a diff or the activity.
    if (app.route.name === 'workspace' || app.route.name === 'surface' || fromVoice) app.go('surface', pid);
  }

  // --- server messages -----------------------------------------------------------------------------------
  function onMessage(msg) {
    if (msg.type === 'pairing') {
      // The same code comes back on every reconnect until it expires; only redraw when it actually changes.
      const changed = msg.code !== app.pairingCode || app.route.name !== 'center';
      app.status = 'pairing';
      app.pairingCode = msg.code;
      app.pairingExpiresAt = Date.now() + (msg.expires_in != null ? msg.expires_in : 600) * 1000;
      app.pairingExpired = app.pairingExpiresAt <= Date.now();
      app.linkDown = false;
      if (changed) app.navigate('center');
      else app.render();
      return;
    }
    if (msg.type === 'pairing_expired') {
      app.pairingExpired = true;
      app.render();
      return;
    }
    if (msg.type === 'hello') {
      app.client = msg.client;
      app.home = msg.home;
      app.projects = msg.projects || {};
      app.approvals = msg.approvals || [];
      if (msg.ui) {
        // Keep any screen version we already have so a reconnect doesn't redraw (and lose focus) for nothing.
        const prev = app.ui.surfaces;
        app.ui.surfaces = {};
        Object.keys(msg.ui.surfaces || {}).forEach((pid) => {
          const s = msg.ui.surfaces[pid];
          app.ui.surfaces[pid] = prev[pid] && prev[pid].version === s.version ? prev[pid] : { surface: s.surface, version: s.version };
        });
        app.ui.data = Object.assign(app.ui.data, msg.ui.data || {});
      }
      const first = !app.everOnline;
      app.everOnline = true;
      if (first || app.route.name === 'center') {
        const single = app.home.projects.length === 1 ? app.home.projects[0].id : null;
        const resume = app.focusProject || msg.focus;
        if (resume && app.projects[resume] && app.projects[resume].connection === 'connected') app.openProject(resume);
        else app.navigate('home', { preselect: single });
      } else {
        app.toast('Back online', 'ok');
      }
      app.overlays.syncApprovals();
      app.render();
      return;
    }
    if (msg.type === 'state') {
      const d = msg.data;
      app.projects[msg.project] = d.snapshot;
      app.home = d.home;
      app.render();
      return;
    }
    handleEvent(msg);
  }

  function handleEvent(ev) {
    const d = ev.data || {};
    switch (ev.type) {
      case 'ui_surface':
        app.ui.surfaces[ev.project] = { surface: d.surface, version: d.version };
        surfaceArrived(ev.project, false);
        break;
      case 'ui_patch':
        app.ui.surfaces[ev.project] = { surface: d.surface, version: d.version, patch: { base: d.base, changed: d.changed || [] } };
        break;
      case 'ui_data':
        if (d.source === 'agents') { app.ui.data.agents = d.data || []; app.ui.data.agents_updated = d.updated || Date.now() / 1000; }
        if (d.source === 'history' && ev.project) app.ui.data.history[ev.project] = d.data;
        break;
      case 'agent_speech':
        app.voice.onSpeech(ev);
        break;
      case 'command_output':
        if (app.route.name === 'surface' && app.screen.onOutput) app.screen.onOutput(ev);
        return;
      case 'approval_required':
        if (!app.approvals.some((a) => a.id === d.approval.id)) app.approvals.push(d.approval);
        native.attention();
        app.overlays.syncApprovals();
        break;
      case 'approval_accepted':
      case 'approval_rejected':
        app.approvals = app.approvals.filter((a) => a.id !== d.approval.id);
        app.overlays.syncApprovals();
        if (ev.type === 'approval_rejected') app.toast('Rejected: ' + d.approval.title, 'bad');
        break;
      case 'voice_reply':
        if (d.speech === 'stop') app.voice.stop();
        if (d.listen === 'off') app.voice.end();
        if (d.navigate && ['surface', 'terminal', 'workspace'].indexOf(d.navigate) >= 0) app.go(d.navigate, ev.project);
        if (d.client_action && app.route.name === 'surface' && app.screen.clientAction) app.screen.clientAction(d.client_action);
        app.overlays.voiceEvent(ev);
        break;
      case 'voice_listening':
      case 'voice_partial':
      case 'voice_command':
        app.overlays.voiceEvent(ev);
        break;
      case 'build_finished':
      case 'deploy_finished':
      case 'tests_finished':
        if (!d.artifact_only) app.overlays.resultEvent(ev);
        break;
      case 'session_connected':
        if (ev.project === (app.route.project || app.focusProject)) app.toast((d.reattached ? 'Reconnected to ' : 'Connected to ') + d.machine, 'ok');
        break;
      case 'session_disconnected':
        if (d.reconnecting && ev.project === (app.route.project || app.focusProject)) app.toast('Connection to the machine lost. Reconnecting…', 'warn');
        break;
      case 'session_error':
        app.toast(d.message, 'bad');
        break;
      case 'task_failed':
        if (d.error && ev.project === (app.route.project || app.focusProject)) app.toast(d.error, 'bad');
        break;
      case 'changes_undone':
        app.toast('Undid changes to ' + d.files.length + ' file' + (d.files.length === 1 ? '' : 's'), 'ok');
        break;
      default:
        break;
    }
    app.render();
  }

  function onStatus(s) {
    const was = app.status + (app.linkDown ? '~' : '');
    if (s === 'online') app.status = 'online';
    else if (s === 'offline' || s === 'connecting') {
      // Keep showing a live pairing code through a short reconnect: the server hands the same one back.
      const keepCode = app.status === 'pairing' && app.pairingCode && app.pairingExpiresAt > Date.now();
      app.status = keepCode ? 'pairing' : s;
      app.linkDown = keepCode;
    }
    if (s === 'online') app.linkDown = false;
    if (!app.everOnline && app.route.name !== 'center') app.navigate('center');
    else if (was !== app.status + (app.linkDown ? '~' : '')) app.render();
  }

  // --- input ---------------------------------------------------------------------------------------------
  createInput((action, down) => {
    if (app.focus.key(action, down)) return;
    if (action === 'select') { if (down) app.focus.select(); return; }
    if (!down) return;
    // Back while the microphone is open just closes it; the screen stays where it is.
    if (action === 'back' && (app.voice.listening || app.voice.speaking) && !app.overlays.approval) { app.voice.stop(); app.voice.end(); return; }
    if (action === 'back') { if (!app.focus.back()) native.exit(); return; }
    if (action === 'menu' || action === 'playpause') {
      if (app.overlays.approval) return;
      if (!app.voice.toggle()) app.overlays.openVoice();
      return;
    }
    if (action === 'up' || action === 'down' || action === 'left' || action === 'right') app.focus.move(action);
  });

  native.onEvent((e) => {
    if (e.type === 'lifecycle' && e.state === 'resume') app.link.reconnectNow();
    if (e.type === 'speech') app.voice.onNative(e);
    if (e.type === 'tts') app.voice.onTts(e);
  });

  app.link = createLink({ server: app.config.server, token: app.config.token, onMessage, onStatus });
  app.navigate('center');
  app.link.connect();
  window.__firedev = app;
  return app;
}
