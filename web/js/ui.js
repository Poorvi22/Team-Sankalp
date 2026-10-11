// Shared helpers: DOM, formatting, icons, toasts.
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  box: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5.3a3 3 0 010 5.4M18 14.8c1.8.7 3 2.4 3 5.2"/>',
  doc: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  cart: '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.7 12.2a1.5 1.5 0 001.5 1.2h8.2a1.5 1.5 0 001.5-1.1L21 8H6"/>',
  truck: '<path d="M2 6h11v10H2zM13 9h4l4 4v3h-8"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
  dollar: '<circle cx="12" cy="12" r="9"/><path d="M14.8 9.2c-.5-1-1.5-1.5-2.8-1.5-1.6 0-2.8.8-2.8 2s1 1.7 2.8 2.1 2.8.9 2.8 2.1-1.2 2-2.8 2c-1.4 0-2.5-.6-3-1.7M12 6v1.7M12 16.3V18"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.5"/>',
  bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13v1.5M15 13v1.5M2 13v3M22 13v3"/>',
  bell: '<path d="M6 16V11a6 6 0 0112 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  send: '<path d="M4 12l16-8-6 16-3-7z"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/>',
  ok: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.5"/>',
  bad: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  revenue: '<path d="M3 17l6-6 4 4 8-9"/><path d="M15 6h6v6"/>',
  orders: '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.7 12.2a1.5 1.5 0 001.5 1.2h8.2a1.5 1.5 0 001.5-1.1L21 8H6"/>',
  inventory: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  suppliers: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5.3a3 3 0 010 5.4M18 14.8c1.8.7 3 2.4 3 5.2"/>',
  tab: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M8 5v4"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  logout: '<path d="M9 4H5a2 2 0 00-2 2v12a2 2 0 002 2h4M16 8l4 4-4 4M20 12H9"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  refresh: '<path d="M20 11a8 8 0 00-14.5-3.5M4 4v4h4M4 13a8 8 0 0014.5 3.5M20 20v-4h-4"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};
export function icon(name, size = 18, cls = '') {
  return `<svg class="ic ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.grid}</svg>`;
}

// ---- number / date formatting ------------------------------------------------------------------------------------
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function usd(v, short = false) {
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e6) return `${s}$${nf2.format(a / 1e6)}M`;
  if (a >= 1e5 || (short && a >= 1e3)) return `${s}$${nf1.format(a / 1e3)}K`;
  return `${s}$${nf0.format(a)}`;
}
export function fmt(v, f) {
  if (v === null || v === undefined || v === '') return '—';
  switch (f) {
    case 'usd': return usd(+v);
    case 'usd_short': return usd(+v, true);
    case 'usd2': return `$${nf2.format(+v)}`;
    case 'int': return nf0.format(+v);
    case 'pct': return `${nf1.format(+v)}%`;
    case 'pctchg': return `${+v >= 0 ? '+' : ''}${nf1.format(v * 100)}%`;
    case 'dec1': return nf1.format(+v);
    case 'dec2': return nf2.format(+v);
    case 'days': return +v >= 900 ? '—' : `${nf1.format(+v).replace(/\.0$/, '')} d`;
    case 'ms': return `${nf0.format(+v)} ms`;
    case 'date': return fmtDate(v);
    case 'datetime': return String(v).replace('T', ' ').slice(0, 16);
    default: return typeof v === 'number' ? nf2.format(v) : esc(v);
  }
}
export function fmtDate(s) {
  if (!s) return '—';
  const [y, m, d] = String(s).slice(0, 10).split('-');
  return `${+d} ${MON[+m - 1]} ${y}`;
}
export function axisFmt(v, f) {
  if (f === 'usd') return usd(v, true);
  if (f === 'pct') return `${nf0.format(v)}%`;
  return nf0.format(v);
}

// ---- status badges -------------------------------------------------------------------------------------------------
const GREEN = ['delivered', 'received', 'approved', 'active', 'in_stock', 'ok'];
const BLUE = ['processing', 'ordered', 'in_transit', 'shipped', 'pending'];
const AMBER = ['pending_approval', 'low_stock', 'on_hold'];
const RED = ['cancelled', 'delayed', 'out_of_stock', 'rejected', 'inactive'];
export function statusClass(v) {
  v = String(v);
  return GREEN.includes(v) ? 'good' : BLUE.includes(v) ? 'info' : AMBER.includes(v) ? 'warn' : RED.includes(v) ? 'bad' : 'muted';
}
export const labelize = (v) => String(v).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
export function badge(v, kind) {
  if (v === null || v === undefined) return '—';
  if (kind === 'days') {
    const d = +v;
    return d >= 900 ? '<span class="muted">—</span>' : `<span class="badge ${d <= 7 ? 'bad' : d <= 14 ? 'warn' : 'good'}">${fmt(d, 'days')}</span>`;
  }
  return `<span class="badge ${statusClass(v)}">${esc(labelize(v))}</span>`;
}

// ---- toasts + lightweight markdown for chat --------------------------------------------------------------------------
export function toast(msg, type = 'info') {
  let box = $('#toasts');
  if (!box) { box = document.createElement('div'); box.id = 'toasts'; document.body.appendChild(box); }
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `${icon(type === 'error' ? 'bad' : type === 'success' ? 'ok' : 'zap', 16)}<span>${esc(msg)}</span>`;
  box.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, 3800);
}

export function md(text) {
  let s = esc(text || '');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;])/g, '$1<em>$2</em>');
  const lines = s.split('\n'), out = [];
  let list = null;
  for (const ln of lines) {
    const b = ln.match(/^\s*- (.*)$/), n = ln.match(/^\s*(\d+)\. (.*)$/);
    if (b || n) {
      const tag = b ? 'ul' : 'ol';
      if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
      out.push(`<li>${b ? b[1] : n[2]}</li>`);
    } else {
      if (list) { out.push(`</${list}>`); list = null; }
      out.push(ln.trim() === '' ? '<div class="sp"></div>' : `<p>${ln}</p>`);
    }
  }
  if (list) out.push(`</${list}>`);
  return out.join('');
}

export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
