// Dependency-free SVG charts (line/area, bars, donut). Return HTML strings.
import { axisFmt, esc, fmt } from './ui.js';

export const PALETTE = ['#2563eb', '#8b5cf6', '#f59e0b', '#16a34a', '#06b6d4', '#ef4444', '#64748b'];
const STATUS = { ok: '#16a34a', warn: '#f59e0b', bad: '#ef4444' };
const STATUS_BY_LABEL = { delivered: '#16a34a', received: '#16a34a', approved: '#16a34a', active: '#16a34a', 'in stock': '#16a34a', 'on time': '#16a34a',
  processing: '#3b82f6', ordered: '#3b82f6', in_transit: '#3b82f6', shipped: '#3b82f6', pending: '#60a5fa', draft: '#94a3b8', pending_approval: '#f59e0b',
  cancelled: '#ef4444', delayed: '#ef4444', late: '#ef4444', rejected: '#ef4444' };

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
}

export function lineChart({ series, secondary, fmt: f = 'int', W = 640, H = 230 }) {
  const pts = series[0].points;
  if (!pts.length) return '<div class="empty">No data</div>';
  const padL = 56, padR = secondary ? 48 : 14, padT = 14, padB = 30;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = niceMax(Math.max(...series.flatMap((s) => s.points.map((p) => p.y)), 0) * 1.05);
  const n = pts.length, X = (i) => padL + (n === 1 ? iw / 2 : (i * iw) / (n - 1)), Y = (v) => padT + ih - (v / max) * ih;
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4, y = Y(v);
    g += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" class="grid"/><text x="${padL - 8}" y="${y + 4}" class="ax" text-anchor="end">${esc(axisFmt(v, f))}</text>`;
  }
  const step = Math.ceil(n / 8);
  pts.forEach((p, i) => { if (i % step === 0 || i === n - 1 && n < 12) g += `<text x="${X(i)}" y="${H - 8}" class="ax" text-anchor="middle">${esc(String(p.x).replace(/ 20(\d\d)$/, " '$1"))}</text>`; });
  let paths = '';
  series.forEach((s, si) => {
    const c = PALETTE[si % PALETTE.length], d = s.points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
    if (si === 0 && series.length === 1) paths += `<path d="${d}L${X(n - 1)},${Y(0)}L${X(0)},${Y(0)}Z" fill="${c}" opacity=".10"/>`;
    const partial = s.points.length > 1 && String(s.points[s.points.length - 1].x).endsWith('(MTD)');
    const solid = partial ? s.points.slice(0, -1) : s.points, dsolid = solid.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
    paths += `<path d="${dsolid}" fill="none" stroke="${c}" stroke-width="2.2" ${s.dashed ? 'stroke-dasharray="5 4"' : ''} stroke-linejoin="round" stroke-linecap="round"/>`;
    if (partial) { const k = s.points.length - 1; paths += `<path d="M${X(k - 1).toFixed(1)},${Y(s.points[k - 1].y).toFixed(1)}L${X(k).toFixed(1)},${Y(s.points[k].y).toFixed(1)}" fill="none" stroke="${c}" stroke-width="2.2" stroke-dasharray="3 4" stroke-linecap="round" opacity=".7"/>`; }
    if (n <= 26) s.points.forEach((p, i) => { paths += `<circle cx="${X(i)}" cy="${Y(p.y)}" r="2.8" fill="#fff" stroke="${c}" stroke-width="1.6"/>`; });
  });
  let sec = '';
  if (secondary) {
    const smax = niceMax(Math.max(...secondary.points.map((p) => p.y)) * 1.05), SY = (v) => padT + ih - (v / smax) * ih;
    const d = secondary.points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${SY(p.y).toFixed(1)}`).join('');
    sec = `<path d="${d}" fill="none" stroke="#16a34a" stroke-width="2" stroke-dasharray="1 0" stroke-linejoin="round"/>`;
    for (let i = 0; i <= 4; i++) sec += `<text x="${W - padR + 6}" y="${Y((max * i) / 4) + 4}" class="ax" fill="#16a34a">${esc(axisFmt((smax * i) / 4, 'int'))}</text>`;
  }
  const legend = series.length > 1 || secondary
    ? `<div class="legend">${series.map((s, i) => `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(s.name)}</span>`).join('')}${secondary ? `<span><i style="background:#16a34a"></i>${esc(secondary.name)}</span>` : ''}</div>` : '';
  const meta = esc(JSON.stringify({ x: pts.map((p) => p.x), s: series.map((s) => ({ n: s.name, v: s.points.map((p) => p.y) })), f, padL, iw, W, sec: secondary ? { n: secondary.name, v: secondary.points.map((p) => p.y) } : null }));
  return `<div class="chart line" data-chart="${meta}"><svg viewBox="0 0 ${W} ${H}" width="100%" role="img">${g}${paths}${sec}<line class="guide" x1="0" x2="0" y1="${padT}" y2="${padT + ih}"/></svg><div class="tip" hidden></div>${legend}</div>`;
}

export function attachTooltips(root) {
  root.querySelectorAll('.chart.line').forEach((el) => {
    const m = JSON.parse(el.dataset.chart), svg = el.querySelector('svg'), tip = el.querySelector('.tip'), guide = el.querySelector('.guide');
    svg.addEventListener('mousemove', (e) => {
      const r = svg.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * m.W, n = m.x.length;
      const i = Math.max(0, Math.min(n - 1, Math.round(((x - m.padL) / m.iw) * (n - 1))));
      const gx = m.padL + (n === 1 ? m.iw / 2 : (i * m.iw) / (n - 1));
      guide.setAttribute('x1', gx); guide.setAttribute('x2', gx); guide.style.opacity = 1;
      tip.hidden = false;
      tip.innerHTML = `<b>${esc(m.x[i])}</b>` + m.s.map((s, k) => `<div><i style="background:${PALETTE[k % PALETTE.length]}"></i>${esc(s.n)}: ${esc(fmt(s.v[i], m.f))}</div>`).join('') + (m.sec ? `<div><i style="background:#16a34a"></i>${esc(m.sec.n)}: ${esc(fmt(m.sec.v[i], 'int'))}</div>` : '');
      const left = (gx / m.W) * r.width;
      tip.style.left = `${Math.min(r.width - tip.offsetWidth - 4, Math.max(4, left - tip.offsetWidth / 2))}px`;
    });
    svg.addEventListener('mouseleave', () => { tip.hidden = true; guide.style.opacity = 0; });
  });
}

export function barChart({ bars, fmt: f = 'int', horizontal = false, W = 520, H = 240, min = 0, compare_label }) {
  if (!bars.length) return '<div class="empty">No data</div>';
  const max = niceMax(Math.max(...bars.map((b) => Math.max(b.value, b.compare || 0)), 0) * 1.05);
  if (horizontal) {
    const rowH = 28, h = bars.length * rowH + 8, lab = 138;
    const lo = min, span = (max - lo) || 1;
    const rows = bars.map((b, i) => {
      const y = 4 + i * rowH, w = Math.max(2, ((b.value - lo) / span) * (W - lab - 78)), cw = b.compare != null ? Math.max(2, ((b.compare - lo) / span) * (W - lab - 78)) : 0;
      return `<text x="${lab - 8}" y="${y + 17}" text-anchor="end" class="ax lab">${esc(String(b.label).length > 22 ? String(b.label).slice(0, 21) + '…' : b.label)}</text>`
        + (cw ? `<rect x="${lab}" y="${y + 14}" width="${cw}" height="5" rx="2.5" fill="#cbd5e1"/>` : '')
        + `<rect x="${lab}" y="${y + 3}" width="${w}" height="${cw ? 11 : 16}" rx="4" fill="${PALETTE[0]}"/><text x="${lab + w + 6}" y="${y + 14}" class="ax val">${esc(fmt(b.value, f))}</text>`;
    }).join('');
    return `<div class="chart bar"><svg viewBox="0 0 ${W} ${h}" width="100%">${rows}</svg></div>`;
  }
  const padL = 56, padB = 44, padT = 10, iw = W - padL - 10, ih = H - padB - padT, bw = Math.min(46, (iw / bars.length) * 0.62);
  let g = '';
  for (let i = 0; i <= 4; i++) { const v = (max * i) / 4, y = padT + ih - (v / max) * ih; g += `<line x1="${padL}" x2="${W - 8}" y1="${y}" y2="${y}" class="grid"/><text x="${padL - 8}" y="${y + 4}" class="ax" text-anchor="end">${esc(axisFmt(v, f))}</text>`; }
  const rects = bars.map((b, i) => {
    const cx = padL + (i + 0.5) * (iw / bars.length), h = (b.value / max) * ih, y = padT + ih - h;
    const lab = String(b.label).length > 11 ? String(b.label).slice(0, 10) + '…' : b.label;
    return `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${h}" rx="4" fill="${PALETTE[0]}"><title>${esc(b.label)}: ${esc(fmt(b.value, f))}</title></rect><text x="${cx}" y="${H - 26}" class="ax" text-anchor="middle">${esc(lab)}</text>`;
  }).join('');
  return `<div class="chart bar"><svg viewBox="0 0 ${W} ${H}" width="100%">${g}${rects}</svg></div>`;
}

export function donutChart({ slices, center, fmt: f = 'int', status_labels }) {
  const total = slices.reduce((a, s) => a + s.value, 0);
  if (!total) return '<div class="empty">No data</div>';
  const R = 62, r = 42, C = 80;
  let a0 = -Math.PI / 2, arcs = '';
  const colors = slices.map((s, i) => STATUS[s.color] || (status_labels && STATUS_BY_LABEL[String(s.label).toLowerCase().replace(/ /g, '_')]) || PALETTE[i % PALETTE.length]);
  slices.forEach((s, i) => {
    const frac = s.value / total, a1 = a0 + frac * 2 * Math.PI - (slices.length > 1 ? 0.025 : 0);
    if (frac > 0.9999) arcs += `<circle cx="${C}" cy="${C}" r="${(R + r) / 2}" fill="none" stroke="${colors[i]}" stroke-width="${R - r}"/>`;
    else {
      const large = a1 - a0 > Math.PI ? 1 : 0, p = (rad, ang) => `${C + rad * Math.cos(ang)},${C + rad * Math.sin(ang)}`;
      arcs += `<path d="M${p(R, a0)}A${R},${R} 0 ${large} 1 ${p(R, a1)}L${p(r, a1)}A${r},${r} 0 ${large} 0 ${p(r, a0)}Z" fill="${colors[i]}"><title>${esc(s.label)}: ${esc(fmt(s.value, f))}</title></path>`;
    }
    a0 += frac * 2 * Math.PI;
  });
  const cv = center ? (center.fmt ? fmt(center.value, center.fmt) : (typeof center.value === 'number' ? fmt(center.value, 'int') : center.value)) : '';
  const mid = center ? `<text x="${C}" y="${C - 1}" text-anchor="middle" class="dv">${esc(cv)}</text><text x="${C}" y="${C + 15}" text-anchor="middle" class="dl">${esc(center.label)}</text>` : '';
  const legend = slices.map((s, i) => `<li><i style="background:${colors[i]}"></i><span>${esc(s.label.replace?.(/_/g, ' ') ?? s.label)}</span><b>${f === 'pct' ? esc(fmt(s.value, 'pct')) : Math.round((s.value / total) * 100) + '%'}</b></li>`).join('');
  return `<div class="chart donut"><svg viewBox="0 0 160 160" width="150" height="150">${arcs}${mid}</svg><ul class="dl-legend">${legend}</ul></div>`;
}
