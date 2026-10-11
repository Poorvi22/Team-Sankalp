// Thin fetch wrapper (cookie session) + NDJSON streaming for the agent.
export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

export function qs(obj) {
  const p = new URLSearchParams();
  Object.entries(obj || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v); });
  const s = p.toString();
  return s ? `?${s}` : '';
}

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (res.status === 401 && !path.includes('/auth/')) window.dispatchEvent(new CustomEvent('auth-expired'));
  if (!res.ok) throw new ApiError((data && (data.error || data.detail)) || `Request failed (${res.status})`, res.status);
  return data;
}

export async function* stream(path, body) {
  const res = await fetch(path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new CustomEvent('auth-expired'));
    throw new ApiError(`Request failed (${res.status})`, res.status);
  }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}
