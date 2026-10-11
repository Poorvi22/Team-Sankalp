// Generic widget renderers: the server describes widgets ({id,type,title,data}); this file draws them.
import { barChart, donutChart, lineChart } from './charts.js';
import { badge, esc, fmt, icon } from './ui.js';

const KPI_ICON = { revenue: 'revenue', orders: 'cart', inventory: 'box', suppliers: 'users', chart: 'chart', ok: 'ok', warn: 'warn', bad: 'bad', star: 'star', clock: 'clock', doc: 'doc', truck: 'truck', bot: 'bot' };
const KPI_TONE = { revenue: 'green', orders: 'blue', inventory: 'purple', suppliers: 'cyan', chart: 'blue', ok: 'green', warn: 'amber', bad: 'red', star: 'amber', clock: 'blue', doc: 'blue', truck: 'blue', bot: 'purple' };

export function kpiRow(data, compact = false) {
  return `<div class="kpis ${compact ? 'compact' : ''}">${data.items.map((it) => {
    const up = it.delta > 0, good = it.delta === 0 || it.delta == null ? null : (up === (it.good !== 'down'));
    const delta = it.delta == null ? (it.sub ? `<div class="kpi-sub">${esc(it.sub)}</div>` : '')
      : `<div class="kpi-delta ${good ? 'good' : 'bad'}" title="${esc(data.compare_label || 'vs previous period')}">${up ? '▲' : '▼'} ${Math.abs(it.delta * 100).toFixed(1)}% <span>vs prev. period</span></div>`;
    return `<div class="kpi"><div class="kpi-ic ${KPI_TONE[it.icon] || 'blue'}">${icon(KPI_ICON[it.icon] || 'chart', 20)}</div><div class="kpi-body"><div class="kpi-label">${esc(it.label)}</div><div class="kpi-value">${esc(fmt(it.value, it.fmt))}</div>${delta}</div></div>`;
  }).join('')}</div>`;
}

export function tableHTML(data, { sort, wid = '', compact = false } = {}) {
  if (!data.rows || !data.rows.length) return `<div class="empty">${esc(data.empty || 'No rows match the current filters.')}</div>`;
  const [sk, sd] = (sort || '').split(':');
  const acts = data.row_actions || [];
  const head = data.columns.map((c) => {
    const active = c.sort && sk === c.sort, next = active && sd === 'asc' ? 'desc' : 'asc';
    return `<th class="${c.fmt && c.fmt !== 'date' ? 'num' : ''} ${c.sort ? 'sortable' : ''}" ${c.sort ? `data-sort="${c.sort}:${next}"` : ''}>${esc(c.label)}${c.sort ? `<span class="sort">${active ? (sd === 'asc' ? '▲' : '▼') : '↕'}</span>` : ''}</th>`;
  }).join('') + (acts.length ? '<th></th>' : '');
  const body = data.rows.map((r, i) => {
    const cells = data.columns.map((c) => {
      let v = r[c.key];
      let html = c.badge ? badge(v, c.badge) : fmt(v, c.fmt);
      if (c.fmt === undefined && !c.badge) html = v === null || v === undefined ? '—' : esc(v);
      if (c.bold) html = `<strong>${html}</strong>`;
      if (c.sub && r[c.sub]) html += `<div class="cell-sub">${esc(r[c.sub])}</div>`;
      return `<td class="${c.fmt && c.fmt !== 'date' ? 'num' : ''}">${html}</td>`;
    }).join('');
    const buttons = acts.filter((a) => !a.when || Object.entries(a.when).every(([k, val]) => (Array.isArray(val) ? val.includes(r[k]) : r[k] === val)))
      .map((a) => `<button class="btn xs ${a.danger ? 'danger' : ''}" data-act="${a.id}" data-w="${wid}" data-i="${i}">${esc(a.label)}</button>`).join('');
    return `<tr data-i="${i}">${cells}${acts.length ? `<td class="acts">${buttons}</td>` : ''}</tr>`;
  }).join('');
  const foot = data.total != null && data.total > data.rows.length ? `<div class="tfoot">Showing ${data.rows.length} of ${data.total.toLocaleString()}</div>` : '';
  return `<div class="tablewrap ${compact ? 'compact' : ''}"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${foot}`;
}

export function renderWidget(w, state) {
  try { return renderWidgetUnsafe(w, state); } catch (e) {
    console.error('widget failed', w.id, e);
    return `<section class="widget card span-${w.span || 12}" data-widget-id="${state.page}.${w.id}"><header><h3>${esc(w.title)}</h3></header><div class="empty">This widget could not be drawn.</div></section>`;
  }
}

function renderWidgetUnsafe(w, state) {
  const d = w.data;
  let body;
  if (!d) body = '<div class="empty">No data</div>';
  else if (w.type === 'kpi_row') return `<div class="widget span-12" data-widget-id="${state.page}.${w.id}" data-widget-type="kpi_row">${kpiRow(d)}</div>`;
  else if (w.type === 'line' && d.bars) body = barChart(d);          // e.g. Analytics grouped by category: same widget, bar form
  else if (w.type === 'line') body = lineChart({ series: d.series, secondary: d.secondary, fmt: d.fmt });
  else if (w.type === 'bar') body = barChart(d);
  else if (w.type === 'donut') body = donutChart(d);
  else if (w.type === 'table') body = tableHTML(d, { sort: state.filters.sort, wid: w.id });
  else body = '';
  const link = d && d.link ? `<a class="viewall" href="#" data-link='${esc(JSON.stringify(d.link))}'>View all</a>` : '';
  return `<section class="widget card span-${w.span || 12}" data-widget-id="${state.page}.${w.id}" data-widget-type="${w.type}"><header><h3>${esc(w.title)}</h3>${link}</header><div class="wbody">${body}</div></section>`;
}
