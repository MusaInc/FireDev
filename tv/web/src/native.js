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
    return {
      server: param('server') || cfg.server || fromPage || 'http://10.0.2.2:8130',
      token: param('token') || cfg.token || null,
      version: cfg.version || 'web',
    };
  },
  speechAvailable() { return !!(N && N.speechAvailable && N.speechAvailable()); },
  startSpeech() { if (N && N.startSpeech) N.startSpeech(); },
  stopSpeech() { if (N && N.stopSpeech) N.stopSpeech(); },
  exit() { if (N && N.exitApp) N.exitApp(); },
  attention() { if (N && N.attention) N.attention(); },
  onEvent(fn) { handler = fn; },
};
