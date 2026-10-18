/**
 * The spoken conversation on the TV: the microphone, the agent's voice, and whose turn it is.
 *
 * - The agent's words arrive as agent_speech events (its acknowledgement, meaningful progress, approvals, and its
 *   final answer once the turn has really ended). They are spoken in order, each exactly once.
 * - ☰ starts a conversation. After a spoken reply the microphone opens again by itself for the follow-up; two
 *   silent turns in a row pause it until ☰ (so a TV across the room isn't listening all evening).
 * - Speaking over the TV interrupts it: what the microphone hears while the TV talks is compared with what the TV
 *   is saying, so its own voice is ignored and only the user's words cut in.
 * - "Stop speaking" and "continue" are handled right here; everything else goes to FireDev and the agent.
 */

import { native } from './native.js';
import { clear, h } from './util.js';

const AUTO_LISTEN_AFTER = { reply: true, firedev: true, approval: true };
const ECHO = 0.6;

function words(t) { return String(t || '').toLowerCase().match(/[a-z0-9']+/g) || []; }

export function echoScore(heard, spoken) {
  const hw = words(heard);
  if (!hw.length) return 0;
  const s = {};
  words(spoken).forEach((w) => { s[w] = 1; });
  return hw.filter((w) => s[w]).length / hw.length;
}

const LOCAL_STOP = /^(stop (talking|speaking|reading)|be quiet|quiet|shush|shut up|silence|that'?s enough|enough)( please)?[.!]?$/i;
const LOCAL_CONTINUE = /^(continue|carry on|go on|keep going)( please)?[.!]?$/i;

export function createVoice(app) {
  const spokenIds = {};
  const spokenOrder = [];
  let timer = 0;
  let listenTimer = 0;
  let localSeq = 0;
  const dock = h('div.dock', { id: 'dock' });
  document.getElementById('stage').appendChild(dock);

  const v = {
    active: false,
    listening: false,
    heardSomething: false,
    speaking: null,
    queue: [],
    caption: '',
    recent: [], // what the TV said, for the echo check: { text, until }
    misses: 0,
    paused: null,
    awaiting: false, // a request was sent; the reply hasn't been spoken yet

    available() { return native.speechAvailable(); },

    /** An agent_speech event from the service. */
    onSpeech(ev) {
      const d = ev.data || {};
      const pid = app.route.project || app.focusProject;
      if (ev.project && pid && ev.project !== pid) return;
      if (!d.id || spokenIds[d.id] || !d.text) return;
      spokenIds[d.id] = 1;
      spokenOrder.push(d.id);
      if (spokenOrder.length > 300) delete spokenIds[spokenOrder.shift()];
      const item = { id: d.id, text: d.text, kind: d.kind || 'reply', who: d.agent || 'FireDev' };
      if (item.kind === 'reply' || item.kind === 'firedev') v.awaiting = false;
      if (item.kind === 'approval') v.queue.unshift(item);
      else v.queue.push(item);
      v.pump();
    },

    sayLocal(text) { v.queue.push({ id: 'local-' + (++localSeq), text, kind: 'firedev', who: 'FireDev' }); v.pump(); },

    pump() {
      if (v.speaking || (v.listening && v.heardSomething)) return;
      const item = v.queue.shift();
      if (!item) { app.render(); return; }
      v.speaking = item;
      v.caption = item.text;
      native.speak(item.id, item.text);
      // If the speech engine never reports back, don't hold the conversation forever.
      clearTimeout(timer);
      timer = setTimeout(() => v.finished(item.id, 'timeout'), 4000 + words(item.text).length * 520);
      app.render();
    },

    onTts(e) {
      if (e.phase === 'start') return;
      v.finished(e.id, e.phase);
    },

    finished(id, why) {
      const cur = v.speaking;
      if (!cur || cur.id !== id) return;
      clearTimeout(timer);
      v.recent.push({ text: cur.text, until: Date.now() + 4000 });
      v.recent = v.recent.slice(-3);
      v.speaking = null;
      if (why !== 'stopped') v.caption = '';
      if (v.queue.length) { v.pump(); return; }
      if (v.active && AUTO_LISTEN_AFTER[cur.kind] && !v.awaiting && why !== 'stopped') v.listenSoon(450);
      app.render();
    },

    /** ☰ on the remote: start (or restart) the conversation and listen now. */
    listen() {
      if (!v.available()) return false;
      v.active = true;
      v.misses = 0;
      if (v.speaking) v.interrupt();
      v.startListening();
      return true;
    },

    toggle() {
      if (v.listening) { v.stopListening(); v.active = false; app.render(); return true; }
      return v.listen();
    },

    listenSoon(ms) {
      clearTimeout(listenTimer);
      listenTimer = setTimeout(() => {
        if (v.active && !v.listening && !v.speaking && !app.overlays.approval) v.startListening();
      }, ms);
    },

    startListening() {
      clearTimeout(listenTimer);
      v.listening = true;
      v.heardSomething = false;
      v.caption = '';
      native.startSpeech();
      app.render();
    },

    stopListening() {
      clearTimeout(listenTimer);
      if (v.listening) native.stopSpeech();
      v.listening = false;
      v.heardSomething = false;
    },

    /** The user started talking over the TV: stop at once and drop progress chatter queued behind it. */
    interrupt() {
      if (!v.speaking) return;
      const cur = v.speaking;
      v.paused = { text: cur.text, at: Date.now() };
      v.queue = v.queue.filter((q) => q.kind === 'approval');
      native.stopSpeaking();
      v.finished(cur.id, 'stopped');
    },

    /** "Stop speaking". */
    stop() {
      v.queue = [];
      if (v.speaking) v.interrupt();
      v.caption = '';
      app.render();
    },

    /** "Goodbye" / Back while listening. */
    end() {
      v.active = false;
      v.stopListening();
      app.render();
    },

    isEcho(text) {
      const now = Date.now();
      const said = (v.speaking ? [v.speaking.text] : []).concat(v.recent.filter((r) => r.until > now).map((r) => r.text));
      return said.some((s) => echoScore(text, s) >= ECHO);
    },

    /** Recognition events from the Fire TV shell. */
    onNative(e) {
      if (e.phase === 'ready') { v.listening = true; app.render(); return; }
      if (e.phase === 'partial') {
        if (!e.text) return;
        if (v.speaking && words(e.text).length >= 2 && !v.isEcho(e.text)) v.interrupt();
        if (!v.isEcho(e.text)) { v.heardSomething = true; v.caption = e.text; }
        app.render();
        return;
      }
      if (e.phase === 'final') {
        v.listening = false;
        v.heardSomething = false;
        const text = (e.text || '').trim();
        if (!text) return v.miss();
        if (v.isEcho(text)) { v.listenSoon(300); return null; }
        v.misses = 0;
        v.caption = text;
        if (LOCAL_STOP.test(text)) { v.stop(); v.listenSoon(300); return null; }
        if (LOCAL_CONTINUE.test(text) && v.paused && Date.now() - v.paused.at < 90000) {
          const again = v.paused;
          v.paused = null;
          v.sayLocal(again.text);
          return null;
        }
        v.awaiting = true;
        app.say(text, 'tv');
        v.pump();
        app.render();
        return null;
      }
      if (e.phase === 'error' || e.phase === 'end') {
        v.listening = false;
        v.heardSomething = false;
        v.miss();
      }
      return null;
    },

    miss() {
      v.misses += 1;
      if (v.active && v.misses < 2 && !v.speaking) v.listenSoon(400);
      if (!v.speaking) v.pump();
      app.render();
    },

    stateFor(p) {
      const agent = (p && p.agent && p.agent.name) || 'The agent';
      if (v.listening) return { key: 'listening', label: v.heardSomething ? 'Listening…' : 'Listening — go ahead' };
      if (v.speaking) return { key: 'speaking', label: (v.speaking.who || agent) + ' is speaking' };
      const t = p && p.task;
      if (t && t.status === 'waiting_approval') return { key: 'approval', label: 'Waiting for your approval' };
      if (t && (t.status === 'running' || t.status === 'queued')) {
        const lc = p.last_command;
        if (t.kind === 'agent' && !(lc && lc.running) && /^(Asking|Thinking)/.test(t.status_text || '')) return { key: 'thinking', label: agent + ' is thinking…' };
        return { key: 'working', label: (t.kind === 'agent' ? agent + ' is working · ' : '') + (t.status_text || 'Working…') };
      }
      const ut = p && p.ui_task;
      if (ut && (ut.status === 'running' || ut.status === 'queued')) return { key: 'thinking', label: agent + ' is building the screen…' };
      if (v.awaiting) return { key: 'thinking', label: 'Thinking…' };
      if (v.active) return { key: 'idle', label: v.available() ? 'Your turn · press ☰ to talk' : 'Say it to your phone or Alexa' };
      return { key: 'idle', label: v.available() ? 'Press ☰ to talk' : 'Press ☰ for voice' };
    },

    /** The small conversation strip shown over every screen except the generated one (which has its own rail). */
    renderDock() {
      const p = app.project();
      const onSurface = app.route.name === 'surface';
      const st = v.stateFor(p);
      const show = !onSurface && app.everOnline && (v.active || v.speaking || v.listening || v.awaiting);
      dock.classList.toggle('show', !!show);
      if (!show) return;
      clear(dock).append(h('div.g-state.' + st.key, null, h('span.g-orb'), h('span.clip', null, st.label)),
        v.caption ? h('div.dock-caption.clamp2', null, v.listening ? '“' + v.caption + '”' : v.caption) : null);
    },
  };
  return v;
}
