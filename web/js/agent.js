// Embedded assistant: streams agent events, applies UI actions directly to the live app (no simulated clicking),
// and renders result cards, plans, confirmations and a per-turn latency trace.
import { stream } from './api.js';
import { barChart, lineChart } from './charts.js';
import { $, $$, badge, esc, fmt, icon, md, sleep } from './ui.js';
import { kpiRow, tableHTML } from './widgets.js';

const TIER = { fast: 'fast path', llm: 'LLM-assisted', fallback: 'clarification', confirm: 'confirmation' };

export class AgentPanel {
  constructor(root, app) { this.root = root; this.app = app; this.queue = Promise.resolve(); this.busy = false; }

  mount() {
    const first = this.app.user.name.split(' ')[0];
    this.root.innerHTML = `
      <div class="agent-head">
        <div class="agent-title">${icon('bot', 20)}<span>AGENT ASSISTANT</span><span class="online"><i></i>Online</span></div>
        <div class="agent-tools">
          <button class="icon-btn" id="agentClear" title="Clear conversation">${icon('refresh', 16)}</button>
          <button class="icon-btn" id="agentClose" title="Hide assistant (Ctrl+K to reopen)">${icon('panel', 17)}</button></div>
      </div>
      <div class="agent-ctx"><span>Business companion · you're on <b id="ctxPage"></b></span><span class="chip-llm" id="llmChip" hidden></span></div>
      <div class="agent-scroll" id="agentScroll"><div class="agent-feed" id="agentFeed"></div></div>
      <div class="agent-foot">
        <div class="suggest-row" id="suggestRow"></div>
        <form id="agentForm" autocomplete="off"><input id="agentInput" type="text" maxlength="500" placeholder="Ask me to navigate, analyze or act…  (Ctrl+K)" aria-label="Ask the assistant"><button class="send" type="submit" aria-label="Send">${icon('send', 18)}</button></form>
        <div class="quick"><button data-q="Open ">${icon('arrow', 13)}Navigate</button><button data-q="Analyze ">${icon('chart', 13)}Analyze</button><button data-q="Create a purchase order for ">${icon('plus', 13)}Create</button><button data-q="Find ">${icon('search', 13)}Find</button></div>
      </div>`;
    this.feed = $('#agentFeed');
    this.scroll = $('#agentScroll');
    this.input = $('#agentInput');
    $('#agentForm').addEventListener('submit', (e) => { e.preventDefault(); const t = this.input.value.trim(); if (t) { this.input.value = ''; this.send(t); } });
    $('#agentClear').addEventListener('click', () => { this.feed.innerHTML = ''; this.welcome(); });
    $('#agentClose').addEventListener('click', () => this.app.toggleAgent(false));
    $$('.quick button', this.root).forEach((b) => b.addEventListener('click', () => { this.input.value = b.dataset.q; this.input.focus(); }));
    this.feed.addEventListener('click', (e) => this.onFeedClick(e));
    window.addEventListener('form-saved', (e) => this.supersede(e.detail.group));
    this.welcome(first);
    this.pageChanged();
    fetch('/api/agent/status', { credentials: 'same-origin' }).then((r) => r.json()).then((s) => {
      const c = $('#llmChip'); c.hidden = false; c.textContent = s.llm_enabled ? `LLM tier on · ${s.llm_model}` : 'Offline mode · no LLM key';
      c.title = s.llm_enabled ? 'Unclear requests are normalised by Claude, then run through the same guarded pipeline.' : 'Everything runs locally. Set ANTHROPIC_API_KEY to enable the optional LLM tier.';
    }).catch(() => {});
  }

  focus() { this.input && this.input.focus(); }

  pageChanged() {
    const ctx = this.app.getContext(), def = this.app.meta.pages.find((p) => p.id === ctx.page);
    if (!def) return;
    $('#ctxPage').textContent = def.title;
    const sugg = (this.app.meta.suggestions[ctx.page] || []).slice(0, 3);
    $('#suggestRow').innerHTML = sugg.map((s) => `<button class="sugg" data-send="${esc(s)}">${esc(s)}</button>`).join('');
  }

  welcome(first) {
    first = first || this.app.user.name.split(' ')[0];
    const ctx = this.app.getContext(), def = this.app.meta.pages.find((p) => p.id === ctx.page);
    const sugg = this.app.meta.suggestions[ctx.page] || this.app.meta.suggestions.dashboard;
    this.feed.insertAdjacentHTML('beforeend', `<div class="msg bot welcome"><div class="avatar">${icon('bot', 16)}</div><div class="bubble">
      <p><b>👋 Hi ${esc(first)}!</b> I'm embedded in BizPilot and can see every page, filter and record you can. Tell me what you need and I'll do it.</p>
      <ul class="caps"><li>${icon('ok', 14)}<span>Navigate to any page and set filters for you</span></li><li>${icon('ok', 14)}<span>Analyze trends, compare periods, explain <i>why</i></span></li><li>${icon('ok', 14)}<span>Fill forms and prepare purchase orders</span></li><li>${icon('ok', 14)}<span>Approve, receive, adjust — always with your confirmation</span></li><li>${icon('ok', 14)}<span>Answer questions about the page you're on${def ? ` (now: ${esc(def.title)})` : ''}</span></li></ul></div></div>
      <div class="suggest"><h4>Suggested insights</h4>${sugg.slice(0, 6).map((s) => `<button class="sugg" data-send="${esc(s)}">${esc(s)}</button>`).join('')}</div>`);
  }

  onFeedClick(e) {
    const send = e.target.closest('[data-send]');
    if (send) { this.send(send.dataset.send); return; }
    const src = e.target.closest('[data-nav]');
    if (src) { const n = JSON.parse(src.dataset.nav); this.app.navigate(n.page, n.filters || {}, { newTab: e.ctrlKey || e.metaKey, flash: true }); return; }
    const tr = e.target.closest('[data-trace]');
    if (tr) { tr.closest('.turn').querySelector('.trace-detail').hidden ^= true; return; }
  }

  atBottom() { return this.scroll.scrollHeight - this.scroll.scrollTop - this.scroll.clientHeight < 120; }
  toBottom(force) { if (force || this.stick) this.scroll.scrollTop = this.scroll.scrollHeight; }

  bubble(role, html, cls = '') {
    const el = document.createElement('div');
    el.className = `msg ${role} ${cls}`;
    el.innerHTML = role === 'bot' ? `<div class="avatar">${icon('bot', 16)}</div><div class="bubble">${html}</div>` : `<div class="bubble">${html}</div><div class="read">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ✓</div>`;
    return el;
  }

  async send(text) {
    if (this.busy) return;
    this.busy = true;
    this.stick = true;
    this.feed.appendChild(this.bubble('user', esc(text)));
    const turn = document.createElement('div');
    turn.className = 'turn';
    turn.innerHTML = `<div class="msg bot typing"><div class="avatar">${icon('bot', 16)}</div><div class="bubble"><span class="dots"><i></i><i></i><i></i></span></div></div>`;
    this.feed.appendChild(turn);
    this.toBottom(true);
    const ctx = this.app.getContext();
    await this.consume(turn, stream('/api/agent/chat', { message: text, context: ctx }), ctx);
    this.busy = false;
  }

  async consume(turn, events, ctx) {
    const st = { steps: null, stepEls: {}, route: null, t0: performance.now(), ctx };
    try {
      for await (const ev of events) {
        await this.handle(turn, ev, st);
        this.toBottom();
      }
    } catch (e) {
      this.add(turn, this.bubble('bot', `<p class="err">Sorry — I lost the connection to the server (${esc(e.message)}).</p>`));
    }
    await this.queue;
    turn.querySelector('.typing')?.remove();
    this.toBottom();
  }

  add(turn, el) { turn.querySelector('.typing')?.remove(); turn.appendChild(el); return el; }

  async handle(turn, ev, st) {
    switch (ev.t) {
      case 'route': st.route = ev; break;
      case 'step': {
        turn.querySelector('.typing')?.remove();
        if (!st.steps) { st.steps = document.createElement('div'); st.steps.className = 'plan'; turn.appendChild(st.steps); }
        let li = st.stepEls[ev.id];
        if (!li) { li = document.createElement('div'); li.className = 'plan-step'; st.steps.appendChild(li); st.stepEls[ev.id] = li; }
        li.className = `plan-step ${ev.state}`;
        li.innerHTML = `<span class="st">${ev.state === 'running' ? '<i class="spin"></i>' : ev.state === 'error' ? icon('bad', 15) : icon('ok', 15)}</span><span>${esc(ev.label)}</span>${ev.ms != null && ev.state === 'done' ? `<em>${ev.ms} ms</em>` : ''}`;
        break;
      }
      case 'ui': {
        // narrate in event order immediately, then perform the action (serialised) and update the line when done
        const line = ev.a === 'refresh' || ev.a === 'close_form' ? null : this.action(turn, ev.a === 'open_form' ? 'edit' : 'compass', this.pendingText(ev));
        this.queue = this.queue.then(() => this.applyUI(line, ev));
        break;
      }
      case 'card': this.add(turn, this.renderCard(ev.card)); break;
      case 'msg': this.add(turn, this.bubble('bot', md(ev.text))); break;
      case 'chips': { const d = document.createElement('div'); d.className = 'chips'; d.innerHTML = ev.items.map((c) => `<button class="sugg" data-send="${esc(c.send)}">${esc(c.label)}</button>`).join(''); this.add(turn, d); break; }
      case 'confirm': this.add(turn, this.confirmCard(turn, ev, st)); break;
      case 'done': await this.queue; this.footer(turn, ev.meta, st); break;
      default: break;
    }
  }

  // ---- UI actions: applied directly to app state, then narrated --------------------------------------------------
  pendingText(ev) {
    if (ev.a === 'navigate') return `Opening <b>${esc(this.app.meta.pages.find((p) => p.id === ev.page)?.title || ev.page)}</b>…`;
    if (ev.a === 'open_form') return `Filling in the <b>${esc(this.app.meta.forms[ev.form].title)}</b> form…`;
    return '';
  }

  async applyUI(line, ev) {
    const app = this.app, set = (html) => { if (line) line.querySelector('span').innerHTML = html; };
    if (ev.a === 'navigate') {
      const title = app.meta.pages.find((p) => p.id === ev.page)?.title || ev.page;
      await app.navigate(ev.page, ev.filters || {}, { newTab: ev.new_tab, flash: true });
      set(`${ev.new_tab ? 'Opened tab' : 'Opened'} <b>${esc(title)}</b>${ev.label ? ` · ${esc(ev.label)}` : ''}`);
    } else if (ev.a === 'open_form') {
      await app.openForm(ev.form, ev.values || {}, { animate: true });
      set(`Filled in the <b>${esc(app.meta.forms[ev.form].title)}</b> form`);
    } else if (ev.a === 'close_form') app.closeForm();
    else if (ev.a === 'refresh') await app.refresh();
  }

  action(turn, ic, html) {
    const d = document.createElement('div');
    d.className = 'action-line';
    d.innerHTML = `${icon(ic === 'compass' ? 'zap' : ic, 14)}<span>${html}</span>`;
    this.add(turn, d);
    this.toBottom();
    return d;
  }

  // ---- cards ----------------------------------------------------------------------------------------------------
  srcChips(sources) {
    if (!sources || !sources.length) return '';
    return `<div class="srcs">${sources.map((s) => `<button class="src" data-nav='${esc(JSON.stringify({ page: s.page, filters: s.filters }))}' title="Open the supporting view (Ctrl-click: new tab)">${icon('link', 12)}${esc(s.label)}</button>`).join('')}</div>`;
  }

  renderCard(c) {
    const el = document.createElement('div');
    el.className = `card-chat c-${c.kind}`;
    let html = '';
    if (c.kind === 'kpi') {
      const up = c.delta > 0, good = c.delta == null ? null : (up === (c.good !== 'down'));
      html = `<div class="ck-t">${esc(c.title)}</div><div class="ck-v">${esc(fmt(c.value, c.fmt))}</div><div class="ck-s">${esc(c.sub || '')}</div>${c.delta != null ? `<div class="pill ${good ? 'good' : 'bad'}">${up ? '▲' : '▼'} ${Math.abs(c.delta * 100).toFixed(1)}% <span>${esc(c.delta_label || '')}</span></div>` : ''}${this.srcChips(c.sources)}`;
    } else if (c.kind === 'chart') {
      const chart = c.shape === 'line' ? lineChart({ series: c.series, fmt: c.fmt, W: 400, H: 190 }) : barChart({ bars: c.bars, fmt: c.fmt, horizontal: c.horizontal, W: 400, H: 220 });
      html = `<div class="ck-t">${esc(c.title)}</div>${chart}${this.srcChips(c.sources)}`;
    } else if (c.kind === 'table') {
      html = `<div class="ck-t">${esc(c.title)}</div>${tableHTML({ columns: c.columns, rows: c.rows }, { compact: true })}${this.srcChips(c.sources)}`;
    } else if (c.kind === 'compare') {
      const down = c.delta < 0, good = (c.delta > 0) === (c.good !== 'down');
      const mx = Math.max(...(c.drivers || []).map((d) => Math.abs(d.delta)), 1);
      html = `<div class="ck-t">${esc(c.title)}</div><div class="cmp"><div><small>${esc(c.b.label)}</small><b>${esc(fmt(c.b.value, c.fmt))}</b></div><div class="arrow">→</div><div><small>${esc(c.a.label)}</small><b>${esc(fmt(c.a.value, c.fmt))}</b></div>
        <div class="pill ${good ? 'good' : 'bad'}">${down ? '▼' : '▲'} ${c.pct == null ? 'n/a' : Math.abs(c.pct * 100).toFixed(1) + '%'}</div></div>
        ${c.drivers && c.drivers.length ? `<div class="drivers"><small>By ${esc(c.driver_dim)}</small>${c.drivers.map((d) => `<div class="drv"><span>${esc(d.name)}</span><div class="bar"><i class="${d.delta < 0 ? 'neg' : 'pos'}" style="width:${Math.max(3, (Math.abs(d.delta) / mx) * 100)}%"></i></div><b class="${d.delta < 0 ? 'neg' : 'pos'}">${esc(fmt(d.delta, c.fmt === 'usd' ? 'usd_short' : c.fmt).replace(/^(\d)/, '+$1'))}</b></div>`).join('')}</div>` : ''}${this.srcChips(c.sources)}`;
    } else if (c.kind === 'analysis') {
      html = `<div class="ck-t">${esc(c.title)}</div><ul class="evidence">${c.bullets.map((b) => `<li>${md(b.text).replace(/^<p>|<\/p>$/g, '')}<button class="src" data-nav='${esc(JSON.stringify({ page: b.source.page, filters: b.source.filters }))}'>${icon('link', 11)}${esc(b.source.label)}</button></li>`).join('')}</ul>`;
    } else if (c.kind === 'report') {
      html = `<div class="ck-t">${esc(c.title)} <small>${esc(c.compare_label)}</small></div>${kpiRow({ items: c.kpis.map((k) => ({ label: k.label, value: k.value, fmt: k.fmt, delta: k.change, good: k.good, icon: 'chart' })), compare_label: '' }, true)}
        <div class="two"><div><h5>Top products</h5>${c.top_products.map((p) => `<div class="li"><span>${esc(p.name)}</span><b>${esc(fmt(p.value, 'usd_short'))}</b></div>`).join('')}</div><div><h5>Top regions</h5>${c.top_regions.map((p) => `<div class="li"><span>${esc(p.name)}</span><b>${esc(fmt(p.value, 'usd_short'))}</b></div>`).join('')}</div></div>
        ${c.risks.length ? `<h5>Risks</h5><ul class="risk">${c.risks.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}${c.recommendations.length ? `<h5>Next steps</h5><ul class="next">${c.recommendations.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}${this.srcChips(c.sources)}`;
    } else if (c.kind === 'entity') {
      html = `<div class="ent-h"><div><div class="ck-t">${esc(c.title)}</div><div class="ck-s">${esc(c.subtitle)}</div></div>${c.status ? badge(c.status) : ''}</div><dl class="facts">${c.facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>${c.table ? tableHTML(c.table, { compact: true }) : ''}${this.srcChips(c.sources)}`;
    }
    el.innerHTML = html;
    return el;
  }

  // ---- human-in-the-loop confirmation ------------------------------------------------------------------------------
  confirmCard(turn, ev, st) {
    const el = document.createElement('div');
    el.className = `confirm ${ev.risk}`;
    el.dataset.group = ev.group || '';
    const lines = ev.lines.slice(0, 6), more = ev.lines.length - lines.length;
    el.innerHTML = `<div class="cf-h">${icon(ev.risk === 'destructive' ? 'warn' : 'check', 16)}<span>${ev.risk === 'destructive' ? 'Destructive action — please confirm' : 'Needs your confirmation'}</span></div>
      <h4>${esc(ev.title)}</h4>
      ${lines.length ? `<ul class="cf-lines">${lines.map((l) => `<li>${md(l).replace(/^<p>|<\/p>$/g, '')}</li>`).join('')}${more > 0 ? `<li class="more">… and ${more} more</li>` : ''}</ul>` : ''}
      ${ev.changes.length ? `<div class="cf-changes">${ev.changes.map((c) => `<div><span>${esc(c.label)}</span><b>${esc(c.before)}</b><i>→</i><b class="after">${esc(c.after)}</b></div>`).join('')}</div>` : ''}
      ${ev.warnings.map((w) => `<div class="cf-warn">${icon('warn', 14)}<span>${esc(w)}</span></div>`).join('')}
      <div class="cf-btns"><button class="btn ghost sm" data-d="cancel">Cancel</button><button class="btn ${ev.risk === 'destructive' ? 'danger-solid' : 'primary'} sm" data-d="approve">${esc(ev.button)}</button></div>`;
    el.querySelectorAll('[data-d]').forEach((b) => b.addEventListener('click', async () => {
      if (el.dataset.done) return;
      el.dataset.done = '1';
      el.querySelectorAll('button').forEach((x) => { x.disabled = true; });
      el.classList.add(b.dataset.d === 'approve' ? 'approved' : 'cancelled');
      const out = document.createElement('div');
      out.className = 'turn sub';
      el.after(out);
      this.stick = true;
      await this.consume(out, stream('/api/agent/confirm', { token: ev.token, decision: b.dataset.d }), st.ctx);
    }));
    return el;
  }

  supersede(group) {
    if (!group) return;
    $$(`.confirm[data-group="${group}"]`, this.feed).forEach((c) => {
      if (c.dataset.done) return;
      c.dataset.done = '1'; c.classList.add('cancelled');
      c.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      c.querySelector('.cf-btns').innerHTML = '<span class="muted-text">You saved this from the form — no need to confirm here.</span>';
    });
  }

  // ---- latency / provenance trace ------------------------------------------------------------------------------------
  footer(turn, meta, st) {
    if (meta.total_ms == null) return;
    const r = st.route, tools = meta.tools || [];
    const d = document.createElement('div');
    d.className = 'trace';
    d.innerHTML = `<div class="trace-line"><span class="lat">${icon('zap', 12)}${meta.total_ms < 1000 ? Math.round(meta.total_ms) + ' ms' : (meta.total_ms / 1000).toFixed(1) + ' s'}</span><span>${TIER[meta.tier] || meta.tier || 'action'}</span>${meta.intent ? `<span>${esc(meta.intent)} · ${(meta.confidence * 100).toFixed(0)}%</span>` : ''}<button data-trace>details</button></div>
      <div class="trace-detail" hidden>
        ${r ? `<div><b>Routing</b> ${r.route_ms} ms · delexicalised: <code>${esc(r.delex || '')}</code></div><div><b>Candidates</b> ${r.candidates.map((c) => `${esc(c.intent)} ${c.score}`).join(' · ')}</div><div><b>Retrieved components</b> ${r.retrieved.map((c) => `<code>${esc(c.id)}</code> ${c.score}`).join(' · ')}</div>` : ''}
        ${tools.length ? `<div><b>Tool calls</b> ${tools.map((t) => `<code>${esc(t.tool)}</code> ${t.ms} ms`).join(' · ')}</div>` : ''}
      </div>`;
    turn.appendChild(d);
  }
}
