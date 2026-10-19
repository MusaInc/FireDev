/**
 * The director: FireDev moves the screen itself, following the conversation and the work, so nobody has to learn
 * where anything is. Every automatic screen change goes through here.
 *
 *   What the user just asked for wins. The service's reply to a spoken request names where to go (a screen, the
 *   diff, the preview, home) and the director goes there.
 *   The work carries the screen along, gently. A generated screen arriving, the page going live, only move the
 *   screen when the user is following that project's work (home, its workspace, its screen or its preview), isn't
 *   in the middle of using the remote, and no approval is open. Screens the user opened deliberately (a diff, the
 *   activity, the projects list, a page they are using) are never pulled away by background events.
 *   Approvals are never answered here: voice only moves the focus to Approve.
 */

const HANDS_OFF_MS = 7000; // after a remote key press, background events leave the screen alone this long
const ASKED_MS = 180000; // a spoken request keeps steering its project's screens this long

export function createDirector(app) {
  const d = {
    lastKey: 0,
    asked: null, // { pid, at, intent, lane }

    /** Every remote key press. */
    key() { d.lastKey = Date.now(); },

    handsOn() { return Date.now() - d.lastKey < HANDS_OFF_MS; },

    askedFor(pid) { return d.asked && d.asked.pid === pid && Date.now() - d.asked.at < ASKED_MS; },

    /** Is the user watching this project's work, so its events may move the screen? */
    following(pid) {
      if (app.overlays.approval) return false;
      const r = app.route;
      if (r.name === 'home') return !app.focusProject || app.focusProject === pid || d.askedFor(pid);
      if (r.name === 'workspace' || r.name === 'surface') return r.project === pid;
      if (r.name === 'preview') return r.project === pid && !(app.screen && app.screen.interacting);
      return false;
    },

    // --- the conversation ----------------------------------------------------------------------------------

    /** voice_command: what was understood. Remember who asked for what, so the work that follows can steer. */
    command(ev) {
      const it = (ev.data && ev.data.intent) || {};
      d.asked = { pid: ev.project, at: Date.now(), intent: it.name, lane: it.args && it.args.lane };
    },

    /** voice_reply: the service says where the answer is. */
    reply(ev) {
      const data = ev.data || {};
      const pid = ev.project || app.route.project || app.focusProject;
      if (pid && data.ok !== false) app.focusProject = pid;
      if (d.asked) d.asked.pid = pid;
      if (data.intent === 'approve_action' || data.navigate === 'approval') { app.overlays.focusApprove(); return; }
      if (app.overlays.approval || !data.navigate) return;
      d.show(data.navigate, pid);
    },

    /** Goes to a place by meaning. Keeps the live preview when the user asked for more work on the same project. */
    show(where, pid) {
      const r = app.route;
      switch (where) {
        case 'home': return app.navigate('home');
        case 'projects': return app.navigate('projects', { preselect: pid });
        case 'workspace':
          // Watching the page while asking for changes: stay on it; the work shows beside it and the page updates.
          if (r.name === 'preview' && r.project === pid) return null;
          if (r.name === 'workspace' && r.project === pid) return null;
          return app.go('workspace', pid);
        case 'surface': return app.go('surface', pid);
        case 'preview': return app.preview.open(pid, { layout: 'split' });
        case 'changes': return pid ? app.navigate('diff', { project: pid }) : null;
        case 'activity': return pid ? app.navigate('activity', { project: pid }) : null;
        case 'terminal': return app.go('terminal', pid);
        default: return null;
      }
    },

    // --- the work ------------------------------------------------------------------------------------------

    /** A generated screen arrived (or changed whole). */
    surface(pid) {
      if (app.route.name === 'surface' && app.route.project === pid) return;
      const asked = d.askedFor(pid) && (d.asked.lane === 'ui' || d.asked.intent === 'agents_dashboard');
      // A screen the user asked for always shows; one the agent put up while you watch its work shows too, but
      // never over the live page (the preview is the better view of a site being built).
      if (asked || (d.following(pid) && !d.handsOn() && app.route.name !== 'preview')) app.go('surface', pid);
    },

    /** The page being built went live (or the finished work has something to see). opts.start: FireDev may start
     *  its own static server for it. */
    live(pid, opts) {
      if (app.route.name === 'preview' && app.route.project === pid) return false;
      if (!d.following(pid) || d.handsOn()) return false;
      app.preview.open(pid, { layout: 'split', start: !!(opts && opts.start) });
      return true;
    },
  };
  return d;
}
