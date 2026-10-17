/**
 * The WebSocket link to the FireDev service: pairing, reconnect with backoff, request/reply, and a heartbeat
 * so a silently dead connection (Wi-Fi drop, TV waking from sleep) is noticed within seconds.
 */

const TOKEN_KEY = 'firedev.token';

function storage(k, v) {
  try {
    if (v === undefined) return window.localStorage.getItem(k);
    if (v === null) window.localStorage.removeItem(k);
    else window.localStorage.setItem(k, v);
  } catch (e) { /* storage blocked: pairing just won't persist */ }
  return null;
}

export function createLink({ server, token, onMessage, onStatus }) {
  let ws = null;
  let seq = 0;
  let delay = 800;
  let closedByUs = false;
  let lastPong = 0;
  let heartbeat = null;
  const waiting = new Map();
  let currentToken = token || storage(TOKEN_KEY);

  function url() {
    const base = server.replace(/^http/, 'ws').replace(/\/$/, '');
    return base + '/ws' + (currentToken ? '?token=' + encodeURIComponent(currentToken) : '');
  }

  function connect() {
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
      if (msg.type === 'paired') {
        currentToken = msg.token;
        storage(TOKEN_KEY, msg.token);
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
    reconnectNow() { if (ws) { try { ws.close(); } catch (e) { /* will reconnect */ } } else connect(); },
    forget() { currentToken = null; storage(TOKEN_KEY, null); },
    get token() { return currentToken; },
  };
}
