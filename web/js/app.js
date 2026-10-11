// App shell: auth, sidebar, workspace tabs, hash routing, row actions, and the UI-control surface used by the agent.
import { AgentPanel } from './agent.js';
import { api, ApiError } from './api.js';
import { closeForm, confirmDialog, initForms, openForm } from './forms.js';
import { PageView } from './page.js';
import { $, $$, esc, fmt, icon, labelize, sleep, toast } from './ui.js';

const S = { user: null, meta: null, tabs: [], active: null, view: null, agent: null, agentOpen: true, seq: 0 };
const LS = 'bizpilot.workspace.v1';

// ------------------------------------------------------------------------------------------------------------ boot
async function boot() {
  window.addEventListener('auth-expired', showLogin);
  try { S.user = await api('/api/auth/me'); await showApp(); } catch { showLogin(); }
}

function showLogin() {
  $('#app').hidden = true; $('#login').hidden = false;
  const form = $('#loginForm'), err = $('#loginError');
  $('#fillDemo').onclick = () => { form.email.value = 'michael@bizpilot.demo'; form.password.value = 'demo1234'; };
  form.onsubmit = async (e) => {
    e.preventDefault(); err.hidden = true;
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try { S.user = await api('/api/auth/login', { method: 'POST', body: { email: form.email.value, password: form.password.value } }); await showApp(); }
    catch (ex) { err.textContent = ex.message; err.hidden = false; }
    btn.disabled = false;
  };
  $('#togglePw').onclick = () => { form.password.type = form.password.type === 'password' ? 'text' : 'password'; };
}

async function showApp() {
  S.meta = await api('/api/meta');
  $('#login').hidden = true; $('#app').hidden = false;
  initForms({ meta: S.meta, onSaved: () => refresh() });
  renderShell();
  S.agent = new AgentPanel($('#agent'), { user: S.user, meta: S.meta, getContext, navigate, openForm: (...a) => openForm(...a), closeForm, refresh, toggleAgent });
  S.agent.mount();
  restore();
  window.addEventListener('hashchange', onHash);
}

// -------------------------------------------------------------------------------------------------------- shell
function renderShell() {
  const u = S.user;
  $('#nav').innerHTML = S.meta.pages.map((p, i) => `${p.id === 'agent_activity' ? '<div class="nav-sep"></div>' : ''}<a href="#/${p.id}" class="nav-item" data-nav-page="${p.id}">${icon(p.icon, 19)}<span>${esc(p.title)}</span>${p.id === 'approvals' ? '<b class="nav-badge" id="navBadge" hidden></b>' : ''}</a>`).join('');
  $('#nav').addEventListener('click', (e) => {
    const a = e.target.closest('[data-nav-page]');
    if (!a) return;
    e.preventDefault();
    navigate(a.dataset.navPage, {}, { newTab: e.ctrlKey || e.metaKey });
  });
  $('#datasetInfo').innerHTML = `<small>Dataset</small><b>24 months</b><span>${esc(S.meta.data_start)} → ${esc(S.meta.as_of)}</span>`;
  $('#userBox').innerHTML = `<button class="user" id="userBtn"><span class="avatar-u">${esc(u.name[0])}</span><span class="who"><b>${esc(u.name)}</b><small>${esc(u.role)}</small></span>${icon('chevron', 14)}</button>
    <div class="menu" id="userMenu" hidden><div class="menu-h"><b>${esc(u.name)}</b><small>${esc(u.email)}</small><small>Approval limit ${fmt(u.approval_limit, 'usd')}</small></div><button id="logoutBtn">${icon('logout', 15)}Sign out</button></div>`;
  $('#userBtn').onclick = (e) => { e.stopPropagation(); $('#userMenu').hidden ^= true; };
  document.addEventListener('click', () => { const m = $('#userMenu'); if (m) m.hidden = true; });
  $('#logoutBtn').onclick = async () => { await api('/api/auth/logout', { method: 'POST' }); localStorage.removeItem(LS); location.hash = ''; location.reload(); };
  $('#bell').onclick = () => navigate('approvals', { status: 'pending' });
  $('#globalSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const t = e.target.value.trim();
    if (!t) return;
    e.target.value = '';
    toggleAgent(true);
    S.agent.send(t);
  });
  $('#agentFab').onclick = () => toggleAgent(true);
  $('#newTab').onclick = () => navigate(S.tabs.find((t) => t.id === S.active)?.page || 'dashboard', {}, { newTab: true });
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); toggleAgent(true); S.agent.focus(); }
    if (e.key === 'Escape' && !$('#modal').hidden) closeForm();
  });
  updateBadge();
}

function updateBadge() {
  const n = S.meta.pending_approvals, b = $('#navBadge'), bell = $('#bellCount');
  if (b) { b.hidden = !n; b.textContent = n; }
  if (bell) { bell.hidden = !n; bell.textContent = n; }
}

function toggleAgent(open) {
  S.agentOpen = open ?? !S.agentOpen;
  document.body.classList.toggle('agent-closed', !S.agentOpen);
  if (S.agentOpen) S.agent.focus();
}

// ------------------------------------------------------------------------------------------------ workspace tabs
const tabLabel = (t) => {
  const def = S.meta.pages.find((p) => p.id === t.page);
  const f = t.filters || {};
  const extra = f.status ? labelize(f.status) : f.search || f.category || f.region || '';
  return (def ? def.title : t.page) + (extra ? ` · ${extra}` : '');
};

function renderTabs() {
  $('#tabs').innerHTML = S.tabs.map((t) => `<div class="tab ${t.id === S.active ? 'active' : ''}" data-tab="${t.id}" title="${esc(tabLabel(t))}" data-page-id="${t.page}">${icon(S.meta.pages.find((p) => p.id === t.page)?.icon || 'grid', 14)}<span>${esc(tabLabel(t))}</span>${S.tabs.length > 1 ? `<button class="x" data-close-tab="${t.id}" aria-label="Close tab">${icon('x', 12)}</button>` : ''}</div>`).join('');
  $$('#tabs .tab').forEach((el) => el.addEventListener('click', (e) => {
    const close = e.target.closest('[data-close-tab]');
    if (close) { e.stopPropagation(); closeTab(close.dataset.closeTab); } else switchTab(el.dataset.tab);
  }));
  $$('[data-nav-page]').forEach((a) => a.classList.toggle('active', a.dataset.navPage === (S.tabs.find((t) => t.id === S.active)?.page)));
}

function persist() {
  try { localStorage.setItem(LS, JSON.stringify({ tabs: S.tabs, active: S.active, seq: S.seq })); } catch { /* storage may be unavailable */ }
  const t = S.tabs.find((x) => x.id === S.active);
  if (t) {
    const q = new URLSearchParams(t.filters).toString();
    history.replaceState(null, '', `#/${t.page}${q ? '?' + q : ''}`);
  }
}

function restore() {
  const fromHash = parseHash();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(LS) || 'null'); } catch { /* ignore */ }
  if (saved && saved.tabs?.length) { S.tabs = saved.tabs.filter((t) => S.meta.pages.some((p) => p.id === t.page)); S.seq = saved.seq || S.tabs.length; S.active = saved.active; }
  if (!S.tabs.length) { S.tabs = [{ id: 't1', page: 'dashboard', filters: {} }]; S.seq = 1; S.active = 't1'; }
  if (!S.tabs.some((t) => t.id === S.active)) S.active = S.tabs[0].id;
  if (fromHash) { const t = S.tabs.find((x) => x.id === S.active); t.page = fromHash.page; t.filters = fromHash.filters; }
  mountActive();
}

function parseHash() {
  const m = location.hash.match(/^#\/([a-z_]+)(?:\?(.*))?$/);
  if (!m || !S.meta.pages.some((p) => p.id === m[1])) return null;
  return { page: m[1], filters: Object.fromEntries(new URLSearchParams(m[2] || '')) };
}

function onHash() {
  const h = parseHash(), t = S.tabs.find((x) => x.id === S.active);
  if (h && t && (h.page !== t.page || JSON.stringify(h.filters) !== JSON.stringify(t.filters))) navigate(h.page, h.filters);
}

// ----------------------------------------------------------------------------------------------- navigation API
function mountActive(flash = []) {
  const tab = S.tabs.find((t) => t.id === S.active);
  S.view = new PageView($('#pageRoot'), S.meta, { onFilters: (f) => navigate(tab.page, f, { sameTab: true }), onRowAction: rowAction, navigate: (p, f) => navigate(p, f), openForm: (f) => openForm(f), onLoaded: () => {} });
  renderTabs(); persist(); S.agent && S.agent.pageChanged();
  return S.view.mount(tab, { flash });
}

/** Navigate the active tab (or open a new one). Resolves once the page has rendered. Used by users and by the agent. */
export async function navigate(page, filters = {}, { newTab = false, flash = false, sameTab = false } = {}) {
  if (!S.meta.pages.some((p) => p.id === page)) { toast(`Unknown page: ${page}`, 'error'); return; }
  const cur = S.tabs.find((t) => t.id === S.active);
  const changed = flash ? Object.keys(filters).filter((k) => !cur || cur.page !== page || JSON.stringify(cur.filters[k]) !== JSON.stringify(filters[k])) : [];
  if (newTab && S.tabs.length >= 12) { toast('Tab limit reached (12) — close one first.', 'error'); newTab = false; }
  if (newTab) {
    S.seq += 1;
    const t = { id: `t${S.seq}`, page, filters: { ...filters } };
    S.tabs.push(t); S.active = t.id;
    return mountActive(changed);
  }
  if (cur.page === page && S.view) {          // same page: keep the DOM, just update filters
    cur.filters = { ...filters };
    renderTabs(); persist();
    return S.view.setFilters(cur.filters, { flash: changed });
  }
  cur.page = page; cur.filters = { ...filters };
  if (flash) $(`[data-nav-page="${page}"]`)?.classList.add('flash');
  return mountActive(changed);
}

function switchTab(id) { if (id === S.active) return; S.active = id; mountActive(); }
function closeTab(id) {
  const i = S.tabs.findIndex((t) => t.id === id);
  if (i < 0 || S.tabs.length === 1) return;
  S.tabs.splice(i, 1);
  if (S.active === id) { S.active = S.tabs[Math.max(0, i - 1)].id; mountActive(); } else { renderTabs(); persist(); }
}

export function getContext() {
  const t = S.tabs.find((x) => x.id === S.active) || {};
  return { page: t.page, filters: t.filters || {}, tab_id: t.id, tabs: S.tabs.map((x) => ({ id: x.id, page: x.page })) };
}

async function refresh() {
  try { S.meta = { ...S.meta, ...(await api('/api/meta')) }; initForms({ meta: S.meta, onSaved: () => refresh() }); updateBadge(); } catch { /* ignore */ }
  if (S.view) await S.view.load();
}

// ------------------------------------------------------------------------------------------------ row actions
async function rowAction(id, row, tab) {
  const done = async (msg) => { toast(msg, 'success'); await refresh(); };
  try {
    switch (id) {
      case 'po_for_product': return openForm('po', { product: row.name, supplier: row.supplier, qty: Math.max(10, Math.round((row.reorder_level * 2 - row.qty) / 10) * 10) });
      case 'edit_stock': return openForm('stock', { product: row.name, warehouse: tab.filters.warehouse || '1', reorder_level: row.reorder_level });
      case 'order_status': return openForm('order_status', { order: row.so_number });
      case 'supplier_hold': {
        const to = row.status === 'active' ? 'on_hold' : 'active';
        if (!(await confirmDialog({ title: `Set ${row.name} to ${labelize(to)}?`, text: to === 'on_hold' ? 'No new purchase orders can be raised while a supplier is on hold.' : 'The supplier becomes available for new purchase orders.', okLabel: 'Apply' }))) return;
        await api('/api/suppliers/update', { method: 'POST', body: { supplier: row.name, status: to } }); return done(`${row.name} is now ${labelize(to)}`);
      }
      case 'po_approve': { const po = row.po_number || row.ref_label; const r = await api('/api/procurement/approve', { method: 'POST', body: { po } }); return done(`${r.po_number} approved`); }
      case 'po_reject': { const po = row.po_number || row.ref_label; if (!(await confirmDialog({ title: `Reject ${po}?`, text: 'The purchase order will be cancelled.', okLabel: 'Reject', danger: true }))) return; const r = await api('/api/procurement/reject', { method: 'POST', body: { po } }); return done(`${r.po_number} rejected`); }
      case 'po_receive': { if (!(await confirmDialog({ title: `Receive ${row.po_number}?`, text: 'Quantities are added to stock at Chicago DC.', okLabel: 'Receive' }))) return; const r = await api('/api/procurement/receive', { method: 'POST', body: { po: row.po_number } }); return done(`${r.po_number} received into ${r.warehouse}`); }
      case 'po_delete': { if (!(await confirmDialog({ title: `Delete draft ${row.po_number}?`, text: 'This cannot be undone.', okLabel: 'Delete', danger: true }))) return; await api('/api/procurement/orders', { method: 'DELETE', body: { po: row.po_number } }); return done(`${row.po_number} deleted`); }
      default: return null;
    }
  } catch (e) { toast(e instanceof ApiError ? e.message : 'Action failed', 'error'); }
}

// A tiny, stable surface for tests / automation and the agent: what is on screen, and how to drive it.
window.BizPilot = { navigate, getContext, snapshot: () => S.view && S.view.snapshot(), openForm: (...a) => openForm(...a), tabs: () => S.tabs };
boot();
