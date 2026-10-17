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
import { Overlays } from './overlays.js';

const SCREENS = { home: HomeScreen, workspace: WorkspaceScreen, activity: ActivityScreen, diff: DiffScreen, center: CenterScreen };

export function createApp() {
  const stage = document.getElementById('stage');
  const screenEl = document.getElementById('screen');
  const toastEl = document.getElementById('toast');

  const app = {
    status: 'connecting',
    everOnline: false,
    pairingCode: null,
    client: null,
    home: { projects: [] },
    projects: {},
    approvals: [],
    focusProject: null,
    route: { name: 'center' },
    screen: null,
    config: native.config(),
  };

  // --- stage scaling: everything is laid out on 1920×1080 -----------------------------------------------
  function fit() {
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.transform = 'scale(' + s + ')';
    stage.style.left = Math.round((window.innerWidth - 1920 * s) / 2) + 'px';
    stage.style.top = Math.round((window.innerHeight - 1080 * s) / 2) + 'px';
  }
  window.addEventListener('resize', fit);
  fit();

  app.focus = createFocus();
  app.overlays = new Overlays(app);

  // --- rendering -----------------------------------------------------------------------------------------
  let frame = 0;
  app.render = function () {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (app.screen) app.screen.update();
      app.overlays.update();
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

  app.say = function (text, source) {
    app.link.voice(text, { source: source || 'tv', project: app.route.project || app.focusProject });
  };

  // --- server messages -----------------------------------------------------------------------------------
  function onMessage(msg) {
    if (msg.type === 'pairing') {
      app.status = 'pairing';
      app.pairingCode = msg.code;
      app.navigate('center');
      return;
    }
    if (msg.type === 'hello') {
      app.client = msg.client;
      app.home = msg.home;
      app.projects = msg.projects || {};
      app.approvals = msg.approvals || [];
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
      case 'voice_listening':
      case 'voice_partial':
      case 'voice_command':
      case 'voice_reply':
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
    const was = app.status;
    if (s === 'online') app.status = 'online';
    else if (s === 'offline' || s === 'connecting') app.status = app.status === 'pairing' && s === 'connecting' ? 'pairing' : s;
    if (!app.everOnline && app.route.name !== 'center') app.navigate('center');
    else if (was !== app.status) app.render();
  }

  // --- input ---------------------------------------------------------------------------------------------
  createInput((action, down) => {
    if (app.focus.key(action, down)) return;
    if (action === 'select') { if (down) app.focus.select(); return; }
    if (!down) return;
    if (action === 'back') { if (!app.focus.back()) native.exit(); return; }
    if (action === 'menu' || action === 'playpause') { app.overlays.openVoice(); return; }
    if (action === 'up' || action === 'down' || action === 'left' || action === 'right') app.focus.move(action);
  });

  native.onEvent((e) => {
    if (e.type === 'lifecycle' && e.state === 'resume') app.link.reconnectNow();
    if (e.type === 'speech') app.overlays.nativeSpeech(e);
  });

  app.link = createLink({ server: app.config.server, token: app.config.token, onMessage, onStatus });
  app.navigate('center');
  app.link.connect();
  window.__firedev = app;
  return app;
}
