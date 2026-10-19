/**
 * The bridge to the Fire TV app shell (window.FireDevNative), with browser fallbacks so the same build runs in
 * a desktop browser at 1920×1080 for development and tests.
 */

const N = window.FireDevNative || null;
let handler = () => {};

window.__fdNative = (e) => handler(typeof e === 'string' ? JSON.parse(e) : e);

function param(name) {
  const m = new RegExp('[?&]' + name + '=([^&#]*)').exec(window.location.search);
  return m ? decodeURIComponent(m[1]) : null;
}

export const native = {
  inShell: !!N,
  config() {
    let cfg = {};
    if (N && N.getConfig) {
      try { cfg = JSON.parse(N.getConfig()); } catch (e) { cfg = {}; }
    }
    // Served by FireDev at <base>/tv/, where <base> may include a path (https://example.com/firedev).
    const fromPage = window.location.protocol.indexOf('http') === 0 ? window.location.origin + window.location.pathname.replace(/\/tv(\/.*)?$/, '') : null;
    // The Fire TV app with its own transports: an empty server means nothing is chosen yet (first run).
    const shellServer = N && cfg.transports ? cfg.server : cfg.server || null;
    return {
      server: param('server') || (shellServer !== null && shellServer !== undefined ? shellServer : fromPage || 'http://10.0.2.2:8130'),
      token: param('token') || cfg.token || null,
      version: cfg.version || 'web',
      previewBase: cfg.previewBase || null,
      connection: cfg.connection || null,
      device: cfg.device || null,
      transports: !!(N && cfg.transports),
    };
  },
  /** Local-first connections, done by the app shell (LAN discovery and TLS, or SSH). */
  startDiscovery() { if (N && N.startDiscovery) N.startDiscovery(); },
  stopDiscovery() { if (N && N.stopDiscovery) N.stopDiscovery(); },
  connectLan(machine) { if (N && N.connectLan) N.connectLan(JSON.stringify(machine)); },
  connectSsh(opts) { if (N && N.connectSsh) N.connectSsh(JSON.stringify(opts)); },
  trustHostKey(fp) { if (N && N.trustHostKey) N.trustHostKey(fp); },
  publicKey() { return N && N.publicKey ? N.publicKey() : null; },
  publicKeyFingerprint() { return N && N.publicKeyFingerprint ? N.publicKeyFingerprint() : null; },
  pairCode(claim, nonce) { return N && N.pairCode ? N.pairCode(claim, nonce) : null; },
  paired() { if (N && N.paired) N.paired(); },
  forgetConnection() { if (N && N.forgetConnection) N.forgetConnection(); },
  speechAvailable() { return !!(N && N.speechAvailable && N.speechAvailable()); },
  /** Text to speech: the shell's Android TextToSpeech, or the browser's speech synthesis in the preview. Reports
   *  {type: 'tts', phase: 'start' | 'done' | 'stopped' | 'error', id} through onEvent. */
  ttsAvailable() { return !!(N && N.speak) || !!window.speechSynthesis; },
  speak(id, text) {
    if (N && N.speak) { N.speak(id, text); return; }
    const ss = window.speechSynthesis;
    if (!ss) { setTimeout(() => handler({ type: 'tts', phase: 'error', id }), 0); return; }
    const u = new window.SpeechSynthesisUtterance(text);
    u.rate = 1.04;
    u.onstart = () => handler({ type: 'tts', phase: 'start', id });
    u.onend = () => handler({ type: 'tts', phase: 'done', id });
    u.onerror = (e) => handler({ type: 'tts', phase: e && e.error === 'interrupted' ? 'stopped' : 'error', id });
    ss.speak(u);
  },
  stopSpeaking() {
    if (N && N.stopSpeaking) N.stopSpeaking();
    else if (window.speechSynthesis) window.speechSynthesis.cancel();
  },
  startSpeech() { if (N && N.startSpeech) N.startSpeech(); },
  stopSpeech() { if (N && N.stopSpeech) N.stopSpeech(); },
  exit() { if (N && N.exitApp) N.exitApp(); },
  attention() { if (N && N.attention) N.attention(); },
  onEvent(fn) { handler = fn; },
};
