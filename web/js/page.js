// Metadata-driven page: header, filter bar (from the page's declared filters) and widget grid.
import { api, qs } from './api.js';
import { attachTooltips } from './charts.js';
import { $, $$, debounce, esc, icon } from './ui.js';
import { renderWidget } from './widgets.js';

const firstSentence = (s) => (s.match(/^[^.]+\./) || [s])[0];

export class PageView {
  constructor(root, meta, handlers) { this.root = root; this.meta = meta; this.h = handlers; this.rows = {}; this.seq = 0; }

  mount(tab, { flash = [] } = {}) {
    this.tab = tab;
    this.def = this.meta.pages.find((p) => p.id === tab.page);
    const d = this.def;
    this.root.setAttribute('data-page-id', d.id);
    this.root.innerHTML = `
      <div class="page-head"><div><h1>${esc(d.title)}</h1><p>${esc(firstSentence(d.description))}</p></div>
        <div class="page-actions">${d.actions.map((a) => `<button class="btn primary" data-open-form="${a.form}">${icon('plus', 16)}${esc(a.label.replace(/^\+\s*/, ''))}</button>`).join('')}</div></div>
      <div class="filters" data-region="filters"></div>
      <div class="grid" data-region="grid"></div>`;
    this.renderFilters(flash);
    this.root.querySelectorAll('[data-open-form]').forEach((b) => b.addEventListener('click', () => this.h.openForm(b.dataset.openForm)));
    return this.load();
  }

  options(f) { return f.options || this.meta.options[f.options_from] || []; }

  renderFilters(flash = []) {
    const box = $('[data-region=filters]', this.root), f = this.tab.filters;
    box.innerHTML = this.def.filters.map((fd) => {
      const v = f[fd.key] ?? fd.default ?? '';
      let ctl;
      if (fd.type === 'date_range') {
        const presets = this.meta.presets, isPreset = presets.some((p) => p.value === v);
        const [a, b] = !isPreset && String(v).includes('..') ? String(v).split('..') : ['', ''];
        ctl = `<select data-k="${fd.key}">${presets.map((p) => `<option value="${p.value}" ${p.value === v ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}<option value="custom" ${!isPreset ? 'selected' : ''}>Custom range…</option></select>
          <span class="custom-range" ${isPreset ? 'hidden' : ''}><input type="date" data-range="a" value="${a}"><span>–</span><input type="date" data-range="b" value="${b}"></span>`;
      } else if (fd.type === 'search') {
        ctl = `<input type="search" data-k="${fd.key}" placeholder="Search…" value="${esc(v)}">`;
      } else {
        const empty = fd.default ? '' : `<option value="">All</option>`;
        ctl = `<select data-k="${fd.key}">${empty}${this.options(fd).map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
      }
      return `<label class="filter ${flash.includes(fd.key) ? 'flash' : ''}" data-filter-key="${fd.key}" title="${esc(fd.description || '')}"><span>${esc(fd.label)}</span>${ctl}</label>`;
    }).join('') + `<button class="btn ghost sm" data-reset>${icon('refresh', 14)} Reset</button>`;
    const set = (k, val) => { const nf = { ...this.tab.filters }; if (val === '' || val == null) delete nf[k]; else nf[k] = val; this.h.onFilters(nf); };
    $$('select[data-k]', box).forEach((s) => s.addEventListener('change', () => {
      if (s.dataset.k === 'date_range' && s.value === 'custom') { $('.custom-range', box).hidden = false; return; }
      set(s.dataset.k, s.value);
    }));
    $$('input[type=search]', box).forEach((i) => i.addEventListener('input', debounce(() => set(i.dataset.k, i.value.trim()), 300)));
    const rng = () => { const a = $('[data-range=a]', box).value, b = $('[data-range=b]', box).value; if (a && b) set('date_range', `${a}..${b}`); };
    $$('[data-range]', box).forEach((i) => i.addEventListener('change', rng));
    $('[data-reset]', box).addEventListener('click', () => this.h.onFilters({}));
  }

  async setFilters(filters, { flash = [] } = {}) {
    this.tab.filters = filters;
    this.renderFilters(flash);
    return this.load();
  }

  async load() {
    const my = ++this.seq, grid = $('[data-region=grid]', this.root);
    grid.classList.add('loading');
    const t0 = performance.now();
    let data;
    try { data = await api('/api/page' + qs({ page: this.tab.page, ...this.tab.filters })); }
    catch (e) { if (my === this.seq) grid.innerHTML = `<div class="card error-card">Could not load this page: ${esc(e.message)}</div>`; return; }
    if (my !== this.seq) return;           // a newer request superseded this one
    this.rows = {};
    data.widgets.forEach((w) => { if (w.type === 'table' && w.data) this.rows[w.id] = w.data.rows; });
    grid.innerHTML = data.widgets.map((w) => renderWidget(w, { page: this.tab.page, filters: this.tab.filters })).join('');
    grid.classList.remove('loading');
    attachTooltips(grid);
    this.lastLoadMs = Math.round(performance.now() - t0);
    this.widgets = data.widgets.map((w) => ({ id: `${this.tab.page}.${w.id}`, type: w.type, title: w.title }));
    $$('th.sortable', grid).forEach((th) => th.addEventListener('click', () => this.h.onFilters({ ...this.tab.filters, sort: th.dataset.sort })));
    $$('[data-act]', grid).forEach((b) => b.addEventListener('click', () => this.h.onRowAction(b.dataset.act, this.rows[b.dataset.w][+b.dataset.i], this.tab)));
    $$('[data-link]', grid).forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); const l = JSON.parse(a.dataset.link); this.h.navigate(l.page, l.filters || {}); }));
    // donut slices on the dashboard drill into inventory by status
    this.h.onLoaded && this.h.onLoaded(data);
  }

  /** What the agent (and tests) can read about the live screen. */
  snapshot() {
    return { page: this.tab.page, filters: this.tab.filters, widgets: this.widgets || [], load_ms: this.lastLoadMs };
  }
}
