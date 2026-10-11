// Modal forms rendered from server metadata. The agent can open one and fill it field by field, visibly.
import { api, qs } from './api.js';
import { $, esc, fmt, icon, sleep, toast } from './ui.js';

let ctx = null;            // { meta, onSaved }
export function initForms(c) { ctx = c; }

function optionsFor(f) {
  if (f.options) return f.options;
  return (ctx.meta.options[f.options_from] || []);
}

function fieldHTML(f) {
  const id = `f_${f.key}`;
  let input;
  if (f.type === 'select') input = `<select id="${id}" name="${f.key}"><option value="">Select…</option>${optionsFor(f).map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('')}</select>`;
  else if (f.type === 'number') input = `<input id="${id}" name="${f.key}" type="number" step="any" min="0" autocomplete="off">`;
  else if (f.type === 'date') input = `<input id="${id}" name="${f.key}" type="date">`;
  else input = `<input id="${id}" name="${f.key}" type="text" autocomplete="off">`;
  return `<label class="field" data-field="${f.key}"><span>${esc(f.label)}${f.required ? ' <b>*</b>' : ''}</span>${input}</label>`;
}

export function closeForm() { const m = $('#modal'); m.hidden = true; m.innerHTML = ''; ctx.openForm = null; }
export const isFormOpen = () => !!ctx.openForm;

export async function openForm(formId, values = {}, { animate = false } = {}) {
  const def = ctx.meta.forms[formId];
  if (!def) return;
  const m = $('#modal');
  m.hidden = false;
  ctx.openForm = formId;
  m.innerHTML = `<div class="modal-card" role="dialog" aria-label="${esc(def.title)}" data-form-id="${formId}">
    <header><h2>${esc(def.title)}</h2><button class="icon-btn" data-close aria-label="Close">${icon('x', 18)}</button></header>
    <p class="muted-text">${esc(def.description)}</p>
    <form novalidate><div class="fields">${def.fields.map(fieldHTML).join('')}</div>
      <div class="form-hint" id="formHint"></div><div class="form-error" id="formError" hidden></div>
      <footer><button type="button" class="btn ghost" data-close>Cancel</button><button type="submit" class="btn primary">${esc(def.submit)}</button></footer></form></div>`;
  const form = $('form', m);
  const get = (k) => form.elements[k];
  const hint = $('#formHint', m);

  let hintBase = '';
  async function autoPrice() {
    if (formId !== 'po') return;
    const s = get('supplier').value, p = get('product').value;
    if (!s || !p) return;
    try {
      const r = await api('/api/lookup/price' + qs({ supplier: s, product: p }));
      if (!r.found) { hintBase = `⚠ ${esc(s)} does not sell ${esc(p)}. Suppliers that do: ${esc(r.suppliers.join(', '))}`; updateTotal(); return; }
      get('unit_price').value = r.unit_price;
      if (!form.dataset.dateTouched) get('expected_at').value = r.expected_at;
      hintBase = `Price list: <b>$${r.unit_price}</b>/${esc(r.unit)} · min order ${r.min_qty.toLocaleString()} · lead time ${r.lead_time_days} days`;
      updateTotal();
    } catch (e) { hintBase = esc(e.message); updateTotal(); }
  }
  function updateTotal() {
    if (formId !== 'po') return;
    const t = (+get('qty').value || 0) * (+get('unit_price').value || 0);
    const extra = t > 50000 ? ' <span class="badge warn">needs approval after submit</span>' : '';
    hint.innerHTML = `${hintBase}${t ? `<br>Total: <b>${fmt(t, 'usd')}</b>${extra}` : ''}`;
  }
  ['supplier', 'product'].forEach((k) => get(k)?.addEventListener('change', autoPrice));
  ['qty', 'unit_price'].forEach((k) => get(k)?.addEventListener('input', updateTotal));
  get('expected_at')?.addEventListener('input', () => { form.dataset.dateTouched = '1'; });

  m.addEventListener('click', (e) => { if (e.target.closest('[data-close]') || e.target === m) closeForm(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('#formError', m), btn = $('button[type=submit]', m);
    const vals = {};
    def.fields.forEach((f) => { const v = get(f.key).value; if (v !== '') vals[f.key] = f.type === 'number' ? +v : v; });
    const missing = def.fields.filter((f) => f.required && vals[f.key] === undefined).map((f) => f.label);
    if (missing.length) { err.hidden = false; err.textContent = `Please fill in: ${missing.join(', ')}`; return; }
    err.hidden = true; btn.disabled = true;
    try {
      const op = ctx.meta.ops[def.op];
      const body = { ...vals };
      if (formId === 'order_status') body.order = vals.order;
      const res = await api(op.path, { method: op.method, body });
      toast(formId === 'po' ? `Created ${res.po.po_number} (draft)` : `${def.title}: saved`, 'success');
      const group = { po: 'po.create', supplier: 'supplier.create' }[formId];
      closeForm();
      window.dispatchEvent(new CustomEvent('form-saved', { detail: { formId, group, res } }));
      ctx.onSaved && ctx.onSaved(formId, res);
    } catch (ex) { err.hidden = false; err.textContent = ex.message; btn.disabled = false; }
  });

  // pre-fill (instant, or typed in front of the user when the agent does it)
  const order = def.fields.filter((f) => values[f.key] !== undefined && values[f.key] !== null);
  for (const f of order) {
    const el = get(f.key), wrap = el.closest('.field');
    if (animate) { wrap.classList.add('agent-active'); el.focus(); await sleep(160); }
    const v = String(values[f.key]);
    if (animate && (f.type === 'text' || f.type === 'number') && v.length < 40) { for (let i = 1; i <= v.length; i++) { el.value = v.slice(0, i); await sleep(Math.max(8, 160 / v.length)); } }
    el.value = v;
    if (el.tagName === 'SELECT' && el.value !== v) { // option label mismatch: try matching by label text
      const opt = [...el.options].find((o) => o.text.toLowerCase().startsWith(v.toLowerCase()));
      if (opt) el.value = opt.value;
    }
    wrap.classList.remove('agent-active'); wrap.classList.add('agent-filled');
    if (animate) await sleep(90);
  }
  if (formId === 'po' && values.supplier && values.product && !values.unit_price) await autoPrice();
  else updateTotal();
  const first = $('.field:not(.agent-filled) input, .field:not(.agent-filled) select', m);
  (animate ? $('button[type=submit]', m) : first)?.focus();
}

// Promise-based confirm dialog (the browser's confirm() is avoided so the UI stays consistent and testable).
export function confirmDialog({ title, text, okLabel = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const m = $('#modal');
    m.hidden = false;
    m.innerHTML = `<div class="modal-card small" role="alertdialog"><header><h2>${esc(title)}</h2></header><p>${esc(text)}</p>
      <footer><button class="btn ghost" data-no>Cancel</button><button class="btn ${danger ? 'danger-solid' : 'primary'}" data-yes>${esc(okLabel)}</button></footer></div>`;
    const done = (v) => { m.hidden = true; m.innerHTML = ''; resolve(v); };
    $('[data-yes]', m).onclick = () => done(true);
    $('[data-no]', m).onclick = () => done(false);
  });
}
