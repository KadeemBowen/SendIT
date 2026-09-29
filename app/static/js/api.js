export const auth = {
  get token() {
    try { return localStorage.getItem('token'); } catch { return null; }
  },
  set(token) {
    try { localStorage.setItem('token', token); } catch { /* storage unavailable */ }
  },
  clear() {
    try { localStorage.removeItem('token'); } catch { /* storage unavailable */ }
  },
};

export async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join(', ') : data.detail;
    const err = new Error(detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Live-update socket that reconnects on its own. Handlers get {type: 'open'} after every (re)connect. */
export function openSocket() {
  const handlers = new Set();
  let ws;
  let closed = false;
  const emit = msg => handlers.forEach(h => h(msg));

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(auth.token || '')}`);
    ws.onopen = () => emit({ type: 'open' });
    ws.onmessage = e => {
      try { emit(JSON.parse(e.data)); } catch { /* ignore malformed */ }
    };
    ws.onclose = () => {
      if (!closed) setTimeout(connect, 2000);
    };
  }
  connect();

  return {
    on(fn) { handlers.add(fn); return () => handlers.delete(fn); },
    send(msg) {
      if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(msg)); return true; }
      return false;
    },
    close() { closed = true; ws && ws.close(); },
  };
}
