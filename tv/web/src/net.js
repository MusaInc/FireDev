/**
 * The WebSocket link to the FireDev service: pairing, reconnect with backoff, request/reply, and a heartbeat
 * so a silently dead connection (Wi-Fi drop, TV waking from sleep) is noticed within seconds.
 */

const TOKEN_KEY = 'firedev.token';
const CLAIM_KEY = 'firedev.pairClaim';

// A random secret this TV keeps while it waits to be paired. The server ties the pairing code to it, so a
// reconnect brings back the same code instead of a new one.
function newClaim() {
  const bytes = new Uint8Array(24);
  if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  let out = '';
  for (let i = 0; i < bytes.length; i++) out += ('0' + bytes[i].toString(16)).slice(-2);
  return out;
}

function storage(k, v) {
  try {
    if (v === undefined) return window.localStorage.getItem(k);
    if (v === null) window.localStorage.removeItem(k);
    else window.localStorage.setItem(k, v);
  } catch (e) { /* storage blocked: pairing just won't persist */ }
  return null;
}

export function createLink({ server, token, onMessage, onStatus, onPaired }) {
  let ws = null;
  let seq = 0;
  let delay = 800;
  let closedByUs = false;
  let lastPong = 0;
  let heartbeat = null;
  const waiting = new Map();
  let currentToken = token || storage(TOKEN_KEY);
  let claim = storage(CLAIM_KEY);
  if (!claim) { claim = newClaim(); storage(CLAIM_KEY, claim); }

  function url() {
    const base = server.replace(/^http/, 'ws').replace(/\/$/, '');
    return base + '/ws?pair=' + claim + (currentToken ? '&token=' + encodeURIComponent(currentToken) : '');
  }

  function connect() {
    if (!server) return; // the Fire TV app hasn't chosen a machine yet
    closedByUs = false;
    onStatus('connecting');
    try {
      ws = new WebSocket(url());
    } catch (e) {
      retry();
      return;
    }
    ws.onopen = () => {
      delay = 800;
      lastPong = Date.now();
      clearInterval(heartbeat);
      heartbeat = setInterval(() => {
        if (!ws || ws.readyState !== 1) return;
        if (Date.now() - lastPong > 12000) { try { ws.close(); } catch (e) { /* closing anyway */ } return; }
        send({ op: 'ping' });
      }, 4000);
    };
    ws.onmessage = (m) => {
      lastPong = Date.now();
      let msg;
      try { msg = JSON.parse(m.data); } catch (e) { return; }
      if (msg.type === 'pong') return;
      if (msg.type === 'pairing' && currentToken) {
        currentToken = null; // the saved token was revoked: this TV pairs again
        storage(TOKEN_KEY, null);
      }
      if (msg.type === 'paired') {
        if (onPaired) onPaired(msg);
        currentToken = msg.token;
        storage(TOKEN_KEY, msg.token);
        claim = newClaim(); // used up: a later unpairing starts from a fresh code
        storage(CLAIM_KEY, claim);
        try { ws.close(); } catch (e) { /* reconnects with the token */ }
        return;
      }
      if (msg.type === 'reply' && waiting.has(msg.id)) {
        const w = waiting.get(msg.id);
        waiting.delete(msg.id);
        w(msg);
        return;
      }
      if (msg.type === 'hello') onStatus('online');
      onMessage(msg);
    };
    ws.onclose = () => {
      clearInterval(heartbeat);
      for (const w of waiting.values()) w({ ok: false, error: 'Connection lost' });
      waiting.clear();
      if (!closedByUs) retry();
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  function retry() {
    onStatus('offline');
    setTimeout(connect, delay);
    delay = Math.min(delay * 1.7, 8000);
  }

  function send(obj) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function command(name, args) {
    return new Promise((resolve) => {
      const id = ++seq;
      waiting.set(id, resolve);
      if (!send({ op: 'cmd', id, name, args: args || {} })) {
        waiting.delete(id);
        resolve({ ok: false, error: 'Not connected' });
      }
      setTimeout(() => {
        if (waiting.has(id)) { waiting.delete(id); resolve({ ok: false, error: 'Timed out' }); }
      }, 120000);
    });
  }

  return {
    connect,
    command,
    voice: (text, extra) => send(Object.assign({ op: 'voice', phase: 'final', text }, extra || {})),
    voicePhase: (phase, extra) => send(Object.assign({ op: 'voice', phase }, extra || {})),
    newCode() { if (!send({ op: 'new_code' })) this.reconnectNow(); },
    reconnectNow() { if (ws) { try { ws.close(); } catch (e) { /* will reconnect */ } } else connect(); },
    forget() { currentToken = null; storage(TOKEN_KEY, null); },
    /** A new machine (the Fire TV app connected somewhere else): its address, and its token if it gave one. */
    setServer(url, token) {
      server = url;
      if (token !== undefined) { currentToken = token || null; storage(TOKEN_KEY, currentToken); }
      delay = 800;
      if (ws && ws.readyState < 2) { closedByUs = false; try { ws.close(); } catch (e) { /* reconnects to the new address */ } } else connect();
    },
    get token() { return currentToken; },
    get claim() { return claim; },
  };
}
