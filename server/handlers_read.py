"""Read-only intent handlers: navigation / UI state, analysis, lookups. Each is a generator of UI events."""
import time

from . import analytics as A, db, nlu, ops, pages, periods as P
from .agentkit import (card, chips, describe_filters, fmt_value, metadata_index, msg, nav, pct_txt, plural, signed_value, src, step, usd)

HANDLERS = {}


def handler(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


# ------------------------------------------------------------------------------------------------------------ helpers
def default_metric(run):
    if run.p.metric:
        return run.p.metric
    return {"finance": "profit", "procurement": "po_spend", "logistics": "on_time_rate", "inventory": "inventory_value",
            "suppliers": "active_suppliers"}.get(run.page, "revenue")


def metric_filters(p, metric):
    f = {}
    if metric in A.SALES_METRICS:
        if p.ents.get("category"):
            f["category"] = p.ents["category"][0].id
        if p.ents.get("region"):
            f["region"] = p.ents["region"][0].id
        prods = [e.id for e in p.ents.get("product", [])]
        if prods:
            f["product_ids"] = prods
        elif p.product_group:
            f["product_ids"] = p.product_group[1]
    return f


def scope_text(p, f):
    bits = []
    if f.get("category"):
        bits.append(f"in {f['category']}")
    if f.get("region"):
        bits.append(f"in {f['region']}")
    if f.get("product_ids"):
        if p.ents.get("product"):
            bits.append("for " + ", ".join(e.name for e in p.ents["product"]))
        elif p.product_group:
            bits.append(f"for “{p.product_group[0]}” products")
    return (" " + " ".join(bits)) if bits else ""


def sales_nav_filters(f, per):
    out = {k: f[k] for k in ("category", "region") if f.get(k)}
    if per:
        out["date_range"] = P.preset_id_for(per, db.as_of())
    return out


def pick_period(run, default="last_12_months"):
    if run.p.periods:
        return run.p.periods[0], False
    return P.preset(default, run.as_of), True


def metric_page(metric):
    return A.METRICS[metric]["page"]


# ----------------------------------------------------------------------------------------------------------- show_view
def _target_pages(run):
    p, cur = run.p, run.page
    ment = list(p.pages_mentioned)
    if p.new_tab and len(ment) >= 2:
        return ment[:6], True
    dom_pages = [d for d, _ in p.statuses]
    if ment:
        for m in ment:
            if m in dom_pages:
                return [m], False
        return [ment[0]], False
    if dom_pages:
        return [dom_pages[0]], False
    if p.ents.get("product") or p.product_group:
        return ["inventory"], False
    if p.ents.get("supplier"):
        return ["suppliers"], False
    if p.ents.get("customer") or p.so_refs:
        return ["sales"], False
    if p.po_refs:
        return ["procurement"], False
    if cur and (p.ents.get("region") or p.ents.get("category") or p.periods or p.sort_hint):
        return [cur], False
    top = metadata_index().search(p.text, k=1, kinds={"page"})
    if top and top[0][1] >= 0.18:
        return [top[0][0]["meta"]["page"]], False
    return [], False


def _snap_rating(v):
    for o in (4.5, 4.0, 3.5):
        if v >= o:
            return str(o).rstrip("0").rstrip(".") if o == 4.0 else str(o)
    return "3.5"


def build_filters(run, pg):
    p = run.p
    defs = {x["key"]: x for x in pages.PAGES[pg]["filters"]}
    f = {}
    if "date_range" in defs and p.periods:
        f["date_range"] = P.preset_id_for(p.periods[0], run.as_of)
    if "status" in defs:
        for dom, val in p.statuses:
            if dom == pg:
                f["status"] = val
                break
    for etype, key in nlu.PAGE_FILTER_ENTITIES.get(pg, {}).items():
        ents = p.ents.get(etype)
        if ents and key in defs:
            f[key] = ents[0].name if etype == "supplier" else str(ents[0].id)
    if "search" in defs:
        if pg == "inventory" and (p.ents.get("product") or p.product_group):
            f["search"] = p.ents["product"][0].name if p.ents.get("product") else p.product_group[0]
        elif pg == "suppliers" and p.ents.get("supplier"):
            f["search"] = p.ents["supplier"][0].name
        elif pg == "sales" and p.ents.get("customer"):
            f["search"] = p.ents["customer"][0].name
        elif pg == "sales" and p.so_refs:
            f["search"] = p.so_refs[0]
        elif pg == "procurement" and p.po_refs:
            f["search"] = p.po_refs[0]
    if pg == "procurement" and p.ents.get("supplier"):
        f["supplier"] = p.ents["supplier"][0].name
    if pg == "suppliers" and p.rating_min:
        f["rating_min"] = _snap_rating(p.rating_min)
    if "sort" in defs and p.sort_hint.get(pg):
        f["sort"] = p.sort_hint[pg]
    if pg == "analytics":
        if p.metric in pages.ANALYTICS_METRICS:
            f["metric"] = p.metric
        if p.group_by:
            f["group_by"] = p.group_by
        elif p.entity_noun in ("category", "region", "product", "customer"):
            f["group_by"] = p.entity_noun
        if p.rx(r"compare|\bvs\b|versus|against|previous|last year"):
            f["compare_to"] = "previous_year" if p.rx(r"last year|year over year|yoy") else "previous_period"
    return f


@handler("show_view")
def h_show_view(run):
    p = run.p
    targets, multi = _target_pages(run)
    if not targets:
        yield msg("I couldn't tell which page you want. Tell me a page name (for example **Inventory**, **Suppliers**, **Procurement**) or what you'd like to see.")
        yield chips(["Open the dashboard", "Show low stock items", "Show delayed shipments", "Show pending approvals"])
        run.status = "clarify"
        return
    shown = []
    for i, pg in enumerate(targets):
        filters = build_filters(run, pg)
        explicit = pg in p.pages_mentioned
        if pg == run.page and not explicit and run.ctx.get("filters"):
            filters = {**run.ctx["filters"], **filters}
        clean, rejected = ops.validate_filters(pg, filters)
        new_tab = p.new_tab or (multi and i > 0) or (multi and i == 0 and False)
        label = describe_filters(pg, clean)
        yield nav(pg, clean, new_tab=new_tab, label=label)
        shown.append((pg, clean, rejected, new_tab))
    if multi:
        yield msg("Opened " + ", ".join(f"**{pages.PAGES[pg]['title']}**" for pg, *_ in shown) + " in separate tabs.")
        return
    pg, clean, rejected, new_tab = shown[0]
    title = pages.PAGES[pg]["title"]
    desc = describe_filters(pg, clean)
    count_txt = ""
    if pg not in ("dashboard", "analytics"):
        data = run.call("page.data", {"page": pg, **clean})
        tbl = next((w for w in reversed(data["widgets"]) if w["type"] == "table" and w["data"]), None)
        if tbl and tbl["data"].get("total") is not None:
            count_txt = f" **{tbl['data']['total']:,}** rows match."
    yield msg(f"{'Opened in a new tab: ' if new_tab else 'Opened '}**{title}**" + (f" — {desc}." if desc else ".") + count_txt
              + (f"\n_Ignored (not valid on this page): {', '.join(f'{k}={v}' for k, v in rejected.items())}_" if rejected else ""))


@handler("reset_filters")
def h_reset(run):
    if not run.page:
        yield msg("There is no page open to reset.")
        return
    yield nav(run.page, {}, label="defaults")
    yield msg(f"Cleared all filters on **{pages.PAGES[run.page]['title']}** — back to the default view.")


@handler("explain_page")
def h_explain_page(run):
    pid = run.page
    if not pid or pid not in pages.PAGES:
        yield msg("You're not on a specific page yet. Ask me to open one, e.g. **“open inventory”**.")
        return
    pg = pages.PAGES[pid]
    filters = run.ctx.get("filters") or {}
    clean, _ = ops.validate_filters(pid, filters)
    data = run.call("page.data", {"page": pid, **clean})
    figs = []
    for w in data["widgets"]:
        if w["type"] == "kpi_row" and w["data"]:
            for it in w["data"]["items"]:
                figs.append(f"{it['label']}: **{fmt_value(it['value'], it['fmt'])}**")
            break
    tbl = next((w for w in data["widgets"] if w["type"] == "table" and w["data"] and w["data"].get("total") is not None), None)
    flt = describe_filters(pid, clean)
    lines = [f"You're on **{pg['title']}** (page id `{pid}`).", pg["description"]]
    lines.append("**Current view:** " + (flt if flt else "default filters") + (f" — {tbl['data']['total']:,} rows in “{tbl['title']}”." if tbl else "."))
    if figs:
        lines.append("**Right now it shows:** " + " · ".join(figs[:4]) + ".")
    lines.append("**Widgets:** " + ", ".join(w["title"] for w in pg["widgets"]) + ".")
    lines.append("**I can set these filters for you:** " + ", ".join(f["label"] for f in pg["filters"]) + ".")
    yield msg("\n\n".join(lines))
    yield chips(pages.SUGGESTIONS.get(pid, [])[:4])


@handler("help")
def h_help(run):
    yield msg("I'm embedded in BizPilot and can act on every page for you:\n"
              "- **Navigate & filter** — “show low stock items”, “show European sales for last quarter”, “open inventory and suppliers in new tabs”\n"
              "- **Analyse** — trends, period comparisons, rankings, stock-out forecasts and *why* a number changed (with links to the evidence)\n"
              "- **Find** — suppliers, products, customers, POs and orders, or the best supplier for a product\n"
              "- **Operate** — create/submit/approve/receive/delete purchase orders, adjust stock, update orders and suppliers (you confirm every change)\n"
              "- **Explain** — “what page am I on?”")
    yield chips(pages.SUGGESTIONS.get(run.page, pages.SUGGESTIONS["dashboard"])[:5])


@handler("unsupported")
def h_unsupported(run):
    yield msg("I can't do that — by design I have no tools for sending email or messages, moving money, or bulk-deleting master data, and I never act outside this application. "
              "What I *can* do: navigate and filter every page, analyse the data, and prepare purchase orders, stock changes, approvals and supplier updates for your confirmation.")
    yield chips(pages.SUGGESTIONS.get(run.page, pages.SUGGESTIONS["dashboard"])[:3])
    run.status = "refused"


@handler("chitchat")
def h_chitchat(run):
    t = run.p.t
    if "thank" in t or t.startswith(("ok", "cool", "great", "nice")):
        yield msg("You're welcome — tell me what to do next.")
    else:
        yield msg(f"Hi {run.user['name'].split()[0]}! What would you like to look at or do?")
        yield chips(pages.SUGGESTIONS.get(run.page, pages.SUGGESTIONS["dashboard"])[:4])


# ---------------------------------------------------------------------------------------------------------- kpi_query
@handler("kpi_query")
def h_kpi(run):
    p = run.p
    metric = default_metric(run)
    info = A.METRICS[metric]
    f = metric_filters(p, metric)
    snap = info.get("snapshot")
    per, defaulted = pick_period(run)
    yield step("q", f"Querying {info['label'].lower()}…")
    t = time.perf_counter()
    if snap:
        val = run.timed("metrics.value", A.value, metric, per.start, per.end, f)
        yield step("q", f"{info['label']} (current snapshot)", "done", round((time.perf_counter() - t) * 1000))
        yield card("kpi", title=info["label"] + scope_text(p, f), value=val, fmt=info["fmt"], sub="current snapshot", sources=[src(f"Open {pages.PAGES[info['page']]['title']}", info["page"])])
        yield msg(f"{info['label']}{scope_text(p, f)} is currently **{fmt_value(val, info['fmt'])}**.")
        return
    prev = P.previous(per, run.as_of)
    val = run.timed("metrics.value", A.value, metric, per.start, per.end, f)
    pv = A.value(metric, prev.start, prev.end, f)
    yield step("q", f"{info['label']} for {per.label}", "done", round((time.perf_counter() - t) * 1000))
    ch = A.pct_change(val, pv)
    pg = info["page"]
    nav_f = sales_nav_filters(f, per) if pg == "sales" else {"date_range": P.preset_id_for(per, run.as_of)} if pg in ("logistics", "finance", "procurement") else {}
    yield card("kpi", title=f"{info['label']}{scope_text(p, f)}", value=val, fmt=info["fmt"], sub=per.label, delta=ch, good=info["good"],
               delta_label=f"vs {prev.label}", sources=[src(f"Open {pages.PAGES[pg]['title']} ({per.label})", pg, nav_f)])
    good = (ch or 0) * (1 if info["good"] == "up" else -1) >= 0
    yield msg(f"**{info['label']}**{scope_text(p, f)} for **{per.label}**: **{fmt_value(val, info['fmt'])}**"
              + (f" — {pct_txt(ch)} vs {prev.label} ({fmt_value(pv, info['fmt'])})." if ch is not None else ".")
              + (f"\n_No period given, so I used the {per.label.lower()}._" if defaulted and not snap else ""))


# ---------------------------------------------------------------------------------------------------------------- trend
@handler("trend")
def h_trend(run):
    p = run.p
    metric = default_metric(run)
    info = A.METRICS[metric]
    if info.get("snapshot"):
        yield msg(f"**{info['label']}** is a point-in-time figure, so it has no trend. I can show its current value or break it down instead.")
        run.status = "clarify"
        return
    f = metric_filters(p, metric)
    per, defaulted = pick_period(run)
    gb = "day" if per.days <= 62 and "day" in info["dims"] else "month"
    t = time.perf_counter()
    yield step("q", f"Loading {info['label'].lower()} by {gb}…")
    pts = run.timed("metrics.series", A.series, metric, per.start, per.end, gb, f)
    st = A.trend_stats(pts, additive=bool(info.get("additive")) and gb == "month")
    yield step("q", f"{len(pts)} data points analysed", "done", round((time.perf_counter() - t) * 1000))
    nf = {"metric": metric if metric in pages.ANALYTICS_METRICS else "revenue", "group_by": "month", "date_range": P.preset_id_for(per, run.as_of)}
    nf.update({k: f[k] for k in ("category", "region") if f.get(k)})
    yield nav("analytics", nf, label=f"{info['label']} by {gb}")
    fm = info["fmt"]
    yield card("chart", title=f"{info['label']}{scope_text(p, f)} — {per.label}", shape="line", fmt=fm,
               series=[{"name": info["label"], "points": [{"x": q["label"] + (" (MTD)" if q.get("partial") else ""), "y": q["value"]} for q in pts]}],
               sources=[src(f"Open in Analytics", "analytics", nf)])
    lines = []
    if st.get("n", 0) >= 2:
        agg = "average" if fm == "pct" else "total"
        if fm == "pct":
            lines.append(f"{info['label']} averaged **{fmt_value(st['mean'], fm)}** over {st['n']} {gb}s.")
        else:
            lines.append(f"{agg.title()} **{fmt_value(st['total'], fm)}** over {st['n']} {gb}s (avg {fmt_value(st['mean'], fm)} per {gb}).")
        hc = st["half_change"]
        if hc is not None and st["n"] >= 4:
            word = "rising" if hc > 0.03 else "falling" if hc < -0.03 else "broadly flat"
            lines.append(f"Trend is **{word}**: the second half of the period is {pct_txt(hc)} vs the first half.")
        lines.append(f"Peak **{st['peak']['label']}** ({fmt_value(st['peak']['value'], fm)}), low **{st['trough']['label']}** ({fmt_value(st['trough']['value'], fm)}).")
        w = st["worst_move"]
        if w and w[1] is not None and w[1] < -0.08 and gb == "month":
            lines.append(f"Biggest drop: **{w[0]['label']}** ({pct_txt(w[1])} vs the previous month{', per day' if info.get('additive') else ''}).")
        if st.get("mom_last") is not None and gb == "month":
            lines.append(f"Latest month ({st['last']['label']}): {pct_txt(st['mom_last'])} vs {st['prev']['label']}.")
    yield msg(f"**{info['label']} trend{scope_text(p, f)}** — {per.label}\n" + "\n".join("- " + l for l in lines)
              + ("\n_No period given, so I used the last 12 months._" if defaulted else ""))
    w = st.get("worst_move")
    if w and w[1] is not None and w[1] < -0.08 and gb == "month":
        yield chips([f"Why did {info['label'].lower()} change in {w[0]['label']}?"])
    run.session["last"] = {"intent": "trend", "metric": metric}


# ---------------------------------------------------------------------------------------------------- compare / explain
def _periods_for_compare(run):
    p = run.p
    if len(p.periods) >= 2:
        a, b = sorted(p.periods[:2], key=lambda x: x.start)
        if b.end == run.as_of and b.kind in ("month", "quarter", "year") and a.days > b.days:
            like = P.previous(b, run.as_of)
            if like.start == a.start:      # to-date period vs the full previous one: compare like for like
                run.detail["like_for_like"] = True
                return b, like, False
        return b, a, False                 # (current=later, previous=earlier)
    if len(p.periods) == 1:
        return p.periods[0], P.previous(p.periods[0], run.as_of), False
    cur = P.preset("last_month", run.as_of)
    return cur, P.previous(cur, run.as_of), True


def _direction_word(delta, good):
    return "rose" if delta > 0 else "fell" if delta < 0 else "was flat"


def narrate(diag, per_a, p):
    """Turn the diagnosis into evidence-linked bullets. Each bullet carries the view that supports it."""
    cmp_ = diag["compare"]
    fm = cmp_["fmt"]
    out, down = [], (cmp_["delta"] or 0) < 0
    cats = [f for f in diag["findings"] if f["kind"] == "driver" and f["dim"] == "category"]
    regs = [f for f in diag["findings"] if f["kind"] == "driver" and f["dim"] == "region"]
    prods = [f for f in diag["findings"] if f["kind"] == "driver" and f["dim"] == "product"]
    unit = fmt_value
    for f in cats[:1]:
        out.append((f"**{f['name']}** is the biggest {'drag' if down else 'contributor'}: {usd(f['a'])} vs {usd(f['b'])} ({pct_txt(f['pct'])}).", f["source"], f"Sales · {f['name']}"))
    for f in regs[:1]:
        out.append((f"By region, **{f['name']}** moved most: {usd(f['a'])} vs {usd(f['b'])} ({pct_txt(f['pct'])}).", f["source"], f"Sales · {f['name']}"))
    if prods:
        out.append(("Products driving it: " + ", ".join(f"**{x['name']}** ({usd(x['delta'], signed=True)})" for x in prods[:3]) + ".", prods[0]["source"], "Sales · products"))
    for f in diag["findings"]:
        k = f["kind"]
        if k == "cancellations":
            out.append((f"Cancellation rate went from **{f['b']:.1f}%** to **{f['a']:.1f}%**.", f["source"], "Cancelled orders"))
        elif k == "on_time":
            out.append((f"On-time delivery moved from **{f['b']:.1f}%** to **{f['a']:.1f}%**.", f["source"], "Logistics"))
        elif k == "delays":
            out.append((f"Delayed shipments rose from **{f['b']:.0f}** to **{f['a']:.0f}**.", f["source"], "Delayed shipments"))
        elif k == "supplier_late":
            out.append((f"Inbound POs from **{f['name']}** ({f['category']}) arrived **{f['a']:.1f} days late** on average (vs {f['b']:.1f} before), across {f['n']} POs.", f["source"], f"POs · {f['name']}"))
        elif k == "expenses":
            out.append((f"Expenses changed from {usd(f['b'])} to {usd(f['a'])} ({pct_txt((f['a'] - f['b']) / f['b'])}).", f["source"], "Finance"))
        elif k == "mix":
            if f["units_pct"] is not None and f["price_pct"] is not None and abs(f["price_pct"]) > 0.05:
                out.append((f"Units sold changed {pct_txt(f['units_pct'])} while revenue per unit changed {pct_txt(f['price_pct'])} — a **mix shift** between cheaper and pricier items, not list-price changes.", f["source"], "Units sold"))
    # conclusion only when the evidence chain is there
    late = [f for f in diag["findings"] if f["kind"] == "supplier_late"]
    canc = any(f["kind"] == "cancellations" and f["a"] > f["b"] for f in diag["findings"])
    top_cat = cats[0]["name"] if cats else None
    concl = None
    if down and top_cat and late and any(l["category"] == top_cat for l in late):
        l = next(l for l in late if l["category"] == top_cat)
        concl = (f"**Likely cause:** a supply problem in {top_cat}. {l['name']} deliveries ran about {l['a']:.0f} days late, which lines up with "
                 + ("the jump in cancellations and " if canc else "") + f"the drop in {top_cat} sales. This is a pattern in the data, not proof — the linked views show the underlying records.")
    elif down and top_cat:
        concl = f"**Most of the change is concentrated in {top_cat}.** I did not find a late-delivery or cancellation signal that explains it, so it looks demand-driven."
    return out, concl


@handler("compare_periods")
def h_compare(run):
    yield from _compare(run, explain=False)


@handler("explain_change")
def h_explain_change(run):
    yield from _compare(run, explain=True)


def _compare(run, explain):
    p = run.p
    metric = default_metric(run)
    info = A.METRICS[metric]
    if info.get("snapshot"):
        yield msg(f"**{info['label']}** is a point-in-time figure, so it can't be compared across periods.")
        run.status = "clarify"
        return
    f = metric_filters(p, metric)
    cur, prev, defaulted = _periods_for_compare(run)
    t = time.perf_counter()
    yield step("c", f"Comparing {info['label'].lower()}: {prev.label} → {cur.label}…")
    diag = run.timed("metrics.compare", A.diagnose, metric, cur, prev, f)
    yield step("c", "Comparison and driver analysis complete", "done", round((time.perf_counter() - t) * 1000))
    c = diag["compare"]
    pf = {"metric": metric if metric in pages.ANALYTICS_METRICS else "revenue", "date_range": P.preset_id_for(cur, run.as_of), "compare_to": "previous_period", "group_by": "category"}
    yield nav("analytics", pf, label=f"{info['label']} · {cur.label} vs {prev.label}")
    word = _direction_word(c["delta"], info["good"])
    lfl = run.detail.get("like_for_like")
    head = f"**{info['label']}**{scope_text(p, f)}: {cur.label} **{fmt_value(c['a']['value'], c['fmt'])}** vs {prev.label} {fmt_value(c['b']['value'], c['fmt'])} — {pct_txt(c['pct'])}"
    if c.get("per_day_pct") is not None:
        head += f" ({pct_txt(c['per_day_pct'])} per day, since the periods differ in length)"
    if lfl:
        head += f" — compared over the same {cur.days} elapsed days"
    drivers = c.get("drivers", {})
    top_dim = next((d for d in ("category", "region") if drivers.get(d)), None)
    yield card("compare", title=f"{info['label']}{scope_text(p, f)}", a=c["a"], b=c["b"], delta=c["delta"], pct=c["pct"], fmt=c["fmt"], good=info["good"],
               drivers=[{"name": r["name"], "delta": r["delta"], "pct": r["pct"]} for r in (drivers.get(top_dim, [])[:3] + drivers.get(top_dim, [])[-2:])] if top_dim else [],
               driver_dim=top_dim, sources=[src("Open in Analytics", "analytics", pf)])
    if not explain:
        text = head + "."
        if top_dim and drivers[top_dim]:
            lo, hi = drivers[top_dim][0], drivers[top_dim][-1]
            text += f"\nBiggest decrease: **{lo['name']}** ({usd(lo['delta'])}); biggest increase: **{hi['name']}** ({usd(hi['delta'])})."
        if defaulted:
            text += "\n_No periods given, so I compared the last full month with the one before._"
        yield msg(text)
        yield chips([f"Why did {info['label'].lower()} change in {cur.label}?"])
        run.session["last"] = {"intent": "compare_periods", "metric": metric}
        return
    premise_down = p.rx(r"\b(drop|fall|fell|decline|dip|lower|decrease|down|slump|reduc|worse|loss)\w*")
    premise_up = p.rx(r"\b(rise|rose|increase|spike|jump|higher|grow|grew|surge|up|improv|better)\w*")
    note = ""
    if premise_down and (c["pct"] or 0) > 0.0 or premise_up and (c["pct"] or 0) < 0.0:
        note = f"\n_Heads-up: you asked about {'a drop' if premise_down else 'an increase'}, but in absolute terms {info['label'].lower()} actually moved the other way."
        if c.get("per_day_pct") is not None:
            note += f" Per day it changed {pct_txt(c['per_day_pct'])}._"
        else:
            note += "_"
    bullets, concl = narrate(diag, cur, p)
    yield card("analysis", title=f"Why {info['label'].lower()} changed: {prev.label} → {cur.label}", bullets=[{"text": b[0], "source": src(b[2], b[1]["page"], b[1]["filters"])} for b in bullets])
    evidence_line = (f"\nI found **{len(bullets)} supporting facts** — each one is listed in the card above and links to the view behind it." if bullets
                     else "\nI didn't find a notable driver beyond normal variation.")
    yield msg(head + "." + note + evidence_line + (f"\n\n{concl}" if concl else "")
              + ("\n_No period given, so I compared the last full month with the one before._" if defaulted else ""))
    run.session["last"] = {"intent": "explain_change", "metric": metric}


# ----------------------------------------------------------------------------------------------------------------- top_n
@handler("top_n")
def h_top_n(run):
    p = run.p
    dim = p.entity_noun or "product"
    asc = p.rx(r"\b(worst|bottom|lowest|least|weakest|poorest|smallest|underperform)\w*")
    n = p.top_n or 5
    per, defaulted = pick_period(run)
    fm = "usd"
    metric = p.metric if p.metric in A.METRICS and dim in A.METRICS[p.metric]["dims"] else None
    if dim == "supplier":
        if p.rx(r"rating|rated"):
            rows = db.q("SELECT name key, rating value FROM suppliers WHERE status='active' ORDER BY rating DESC, on_time_pct DESC")
            label, fm = "Rating", "dec1"
        elif p.rx(r"on[- ]?time|reliab|punctual"):
            rows = db.q("SELECT name key, on_time_pct value FROM suppliers WHERE status='active' ORDER BY on_time_pct DESC")
            label, fm = "On-time delivery", "pct"
        else:
            rows = A.series("po_spend", per.start, per.end, "supplier")
            label = "Procurement spend"
    else:
        metric = metric or "revenue"
        f = metric_filters(p, metric)
        f.pop("product_ids", None) if dim == "product" else None
        rows = run.timed("metrics.series", A.series, metric, per.start, per.end, dim, f)
        label, fm = A.METRICS[metric]["label"], A.METRICS[metric]["fmt"]
    rows = sorted(rows, key=lambda r: r["value"], reverse=not asc)[:n]
    if not rows:
        yield msg("No data for that selection.")
        return
    word = "Bottom" if asc else "Top"
    title = f"{word} {len(rows)} {dim}s by {label.lower()}" + ("" if dim == "supplier" and fm != "usd" else f" — {per.label}")
    if dim == "supplier":
        yield nav("suppliers", {"sort": {"Rating": "rating:desc", "On-time delivery": "on_time_pct:desc"}.get(label, "spend:desc")}, label="sorted")
    elif dim in ("category", "region", "product", "customer"):
        yield nav("analytics", {"metric": metric, "group_by": dim, "date_range": P.preset_id_for(per, run.as_of)}, label=f"{label} by {dim}")
    yield card("chart", title=title, shape="bar", fmt=fm, bars=[{"label": r["key"], "value": r["value"]} for r in rows], horizontal=True,
               sources=[src("Open in Analytics" if dim != "supplier" else "Open Suppliers", "analytics" if dim != "supplier" else "suppliers")])
    yield msg(f"**{title}**\n" + "\n".join(f"{i}. **{r['key']}** — {fmt_value(r['value'], fm)}" for i, r in enumerate(rows, 1))
              + ("\n_No period given, so I used the last 12 months._" if defaulted and dim != "supplier" else ""))


# ------------------------------------------------------------------------------------------------------ stockout_forecast
@handler("stockout_forecast")
def h_forecast(run):
    p = run.p
    h = p.horizon_days or 14
    wid = int(p.ents["warehouse"][0].id) if p.ents.get("warehouse") else None
    t = time.perf_counter()
    yield step("f", f"Projecting stock cover for the next {h} days…")
    rows = run.timed("inventory.forecast", A.stockout_forecast, h, wid)
    if p.ents.get("category"):
        rows = [r for r in rows if r["category"] == p.ents["category"][0].id]
    yield step("f", f"{len(rows)} products at risk", "done", round((time.perf_counter() - t) * 1000))
    nf = {"sort": "days_left:asc"}
    if wid:
        nf["warehouse"] = str(wid)
    if p.ents.get("category"):
        nf["category"] = p.ents["category"][0].id
    yield nav("inventory", nf, label="sorted by days left")
    if not rows:
        yield msg(f"Good news — no product is projected to run out within **{h} days** based on the last 60 days of demand.")
        return
    yield card("table", title=f"Likely to run out within {h} days", columns=[{"key": "name", "label": "Product", "bold": True}, {"key": "qty", "label": "Stock", "fmt": "int"},
                                                                          {"key": "daily_demand", "label": "Daily demand", "fmt": "dec1"}, {"key": "days_left", "label": "Days left", "fmt": "days", "badge": "days"},
                                                                          {"key": "supplier", "label": "Primary supplier"}], rows=rows[:12],
               sources=[src("Open Inventory", "inventory", nf)])
    names = ", ".join(f"**{r['name']}** ({r['days_left']:.0f}d)" for r in rows[:4])
    yield msg(f"**{plural(len(rows), 'product')}** will likely run out within {h} days (stock ÷ average daily demand of the last 60 days): {names}" + (" …" if len(rows) > 4 else "") + ".")
    yield chips([f"Create purchase order drafts for items running out in {h} days"] + ([f"Compare top 3 suppliers for {rows[0]['name']} with best pricing"] if rows else []))


# ------------------------------------------------------------------------------------------------------ compare_suppliers
@handler("compare_suppliers")
def h_compare_suppliers(run):
    p = run.p
    if p.ents.get("product"):
        pids, term = [e.id for e in p.ents["product"]], p.ents["product"][0].name
    elif p.product_group:
        pids, term = p.product_group[1], p.product_group[0]
    else:
        yield msg("Which product should I compare suppliers for? For example: **“compare top 3 suppliers for Steel Rod with best pricing”**.")
        run.status = "clarify"
        return
    crit = "price" if p.rx(r"price|pricing|cheap|cost|afford") else "lead_time" if p.rx(r"lead time|fast|quick|deliver time|soonest") else "reliability" if p.rx(r"reliab|on[- ]?time|dependab|punctual|quality") else "balanced"
    n = p.top_n or 3
    t = time.perf_counter()
    yield step("s", f"Ranking suppliers for “{term}” by {crit.replace('_', ' ')}…")
    rows = run.timed("suppliers.compare", A.rank_suppliers, pids, crit)[:n]
    yield step("s", f"{len(rows)} suppliers ranked", "done", round((time.perf_counter() - t) * 1000))
    if not rows:
        yield msg(f"No active supplier offers a product matching “{term}”.")
        return
    single = len(pids) == 1
    cols = [{"key": "supplier", "label": "Supplier", "bold": True}, {"key": "country", "label": "Country"}]
    for r in rows:
        r["unit_price"] = r["items"][0]["unit_price"] if single else None
        r["covers"] = f"{r['n_products']}/{len(pids)}"
        r["avg_lead_txt"] = r["avg_lead"]
    cols += ([{"key": "unit_price", "label": "Unit price", "fmt": "usd2"}] if single else [{"key": "covers", "label": "Products covered"}, {"key": "price_index", "label": "Price index (1.00 = cheapest)", "fmt": "dec2"}])
    cols += [{"key": "avg_lead", "label": "Lead time", "fmt": "days"}, {"key": "on_time_pct", "label": "On-time", "fmt": "pct"}, {"key": "rating", "label": "Rating", "fmt": "dec1"},
             {"key": "score", "label": "Score", "fmt": "dec1"}]
    sf = {"category": db.q1("SELECT category FROM products WHERE id=?", (pids[0],))["category"], "sort": "on_time_pct:desc"}
    yield nav("suppliers", sf, label="suppliers in this category")
    yield card("table", title=f"Suppliers for “{term}” — ranked by {crit.replace('_', ' ')}", columns=cols,
               rows=[{k: v for k, v in r.items() if k != "items"} for r in rows], sources=[src("Open Suppliers", "suppliers", sf)])
    best = rows[0]
    price_txt = f" at **${best['unit_price']:,.2f}/unit**" if single else ""
    yield msg(f"**Recommended: {best['supplier']}**{price_txt} — {best['avg_lead']:.0f}-day lead time, {best['on_time_pct']:.0f}% on-time, rating {best['rating']:.1f}. "
              + (f"Runner-up: {rows[1]['supplier']}." if len(rows) > 1 else "") + (f"\n_“{term}” matches {len(pids)} products; scores average over the products each supplier offers._" if not single else ""))
    if single:
        yield chips([f"Create a purchase order for {best['items'][0]['product']} from {best['supplier']}"])


# ------------------------------------------------------------------------------------------------------- entity_lookup
def _po_detail(ref):
    po = db.q1("SELECT po.*, s.name supplier FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE upper(po.po_number)=upper(?)", (ref,))
    if not po:
        return None
    items = db.q("SELECT p.name, i.qty, i.unit_price FROM po_items i JOIN products p ON p.id=i.product_id WHERE i.po_id=?", (po["id"],))
    return po, items


@handler("entity_lookup")
def h_lookup(run):
    p = run.p
    if p.po_refs:
        d = _po_detail(p.po_refs[0])
        if not d:
            yield msg(f"I can't find **{p.po_refs[0]}**.")
            run.status = "error"
            return
        po, items = d
        yield nav("procurement", {"search": po["po_number"]}, label=po["po_number"])
        yield card("entity", title=po["po_number"], subtitle=f"Purchase order · {po['supplier']}", status=po["status"],
                   facts=[("Total", usd(po["total"])), ("Created", po["created_at"]), ("Expected", po["expected_at"]), ("Received", po["received_at"] or "—"), ("Created by", po["created_by"])],
                   table={"columns": [{"key": "name", "label": "Product"}, {"key": "qty", "label": "Qty", "fmt": "int"}, {"key": "unit_price", "label": "Unit price", "fmt": "usd2"}], "rows": items})
        yield msg(f"**{po['po_number']}** is **{po['status'].replace('_', ' ')}** — {usd(po['total'])} with {po['supplier']}, expected {po['expected_at']}.")
        return
    if p.so_refs:
        o = db.q1("SELECT o.*, c.name customer FROM sales_orders o JOIN customers c ON c.id=o.customer_id WHERE upper(o.so_number)=upper(?)", (p.so_refs[0],))
        if not o:
            yield msg(f"I can't find **{p.so_refs[0]}**.")
            run.status = "error"
            return
        sh = db.q1("SELECT * FROM shipments WHERE order_id=?", (o["id"],))
        items = db.q("SELECT pr.name, i.qty, i.unit_price FROM so_items i JOIN products pr ON pr.id=i.product_id WHERE i.order_id=?", (o["id"],))
        yield nav("sales", {"search": o["so_number"], "date_range": "last_24_months"}, label=o["so_number"])
        facts = [("Customer", o["customer"]), ("Region", o["region"]), ("Order date", o["order_date"]), ("Promised", o["promised_date"]), ("Value", usd(o["total"]))]
        if sh:
            facts += [("Carrier", sh["carrier"]), ("Shipment", sh["status"].replace("_", " ") + (f" (+{sh['delay_days']}d)" if sh["delay_days"] else ""))]
        yield card("entity", title=o["so_number"], subtitle="Sales order", status=o["status"], facts=facts,
                   table={"columns": [{"key": "name", "label": "Product"}, {"key": "qty", "label": "Qty", "fmt": "int"}, {"key": "unit_price", "label": "Unit price", "fmt": "usd2"}], "rows": items})
        yield msg(f"**{o['so_number']}** for {o['customer']} is **{o['status']}**" + (f"; shipment {sh['status'].replace('_', ' ')} via {sh['carrier']}" if sh else "") + f". Promised {o['promised_date']}.")
        return
    if p.ents.get("supplier"):
        s = db.q1("SELECT * FROM suppliers WHERE id=?", (p.ents["supplier"][0].id,))
        spend = db.scalar("SELECT SUM(total) FROM purchase_orders WHERE supplier_id=? AND status IN ('approved','ordered','received') AND created_at>=?", (s["id"], (run.as_of.replace(day=1) - P.timedelta(days=365)).isoformat()))
        late = db.q1("SELECT COUNT(*) n, AVG(julianday(received_at)-julianday(expected_at)) late FROM purchase_orders WHERE supplier_id=? AND status='received' AND expected_at>=?", (s["id"], (run.as_of - P.timedelta(days=365)).isoformat()))
        openpo = db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id=? AND status IN ('draft','pending_approval','approved','ordered')", (s["id"],))
        prods = [r["name"] for r in db.q("SELECT p.name FROM supplier_prices sp JOIN products p ON p.id=sp.product_id WHERE sp.supplier_id=? ORDER BY p.name", (s["id"],))]
        yield nav("suppliers", {"search": s["name"]}, label=s["name"])
        yield card("entity", title=s["name"], subtitle=f"Supplier · {s['category']} · {s['country']}", status=s["status"],
                   facts=[("Rating", f"{s['rating']:.1f} / 5"), ("On-time (reported)", f"{s['on_time_pct']:.0f}%"), ("Lead time", f"{s['lead_time_days']} days"), ("Payment terms", s["payment_terms"]),
                          ("Spend, last 12 mo", usd(spend)), ("Open POs", str(openpo)), ("Avg delivery delay (12 mo)", f"{(late['late'] or 0):+.1f} days over {late['n']} POs"), ("Supplies", ", ".join(prods[:6]) + (" …" if len(prods) > 6 else ""))],
                   sources=[src("Open POs", "procurement", {"supplier": s["name"]})])
        yield msg(f"**{s['name']}** ({s['country']}, {s['category']}) is **{s['status'].replace('_', ' ')}** — rating {s['rating']:.1f}, {s['on_time_pct']:.0f}% on-time, {s['lead_time_days']}-day lead time. "
                  f"Spend over the last 12 months: {usd(spend)}; {openpo} open POs.")
        return
    if p.ents.get("product") or p.product_group:
        if p.ents.get("product"):
            pr = db.q1("SELECT * FROM products WHERE id=?", (p.ents["product"][0].id,))
        else:
            ids = p.product_group[1]
            if len(ids) > 1:
                names = [r["name"] for r in db.q(f"SELECT name FROM products WHERE id IN ({','.join('?' * len(ids))})", ids)]
                yield msg(f"“{p.product_group[0]}” matches {len(ids)} products — which one? " + ", ".join(f"**{n}**" for n in names))
                yield chips([f"Details for {n}" for n in names[:4]])
                run.status = "clarify"
                return
            pr = db.q1("SELECT * FROM products WHERE id=?", (ids[0],))
        stock = A.stock_rows()
        row = next((r for r in stock if r["pid"] == pr["id"]), None)
        wh = db.q("SELECT w.name, i.qty, i.reorder_level FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id WHERE i.product_id=? ORDER BY w.id", (pr["id"],))
        offers = sorted(A.supplier_offers((pr["id"],)), key=lambda o: o["unit_price"])
        rev = A.value("revenue", *(lambda per: (per.start, per.end))(P.preset("last_12_months", run.as_of)), {"product_ids": [pr["id"]]})
        yield nav("inventory", {"search": pr["name"]}, label=pr["name"])
        yield card("entity", title=pr["name"], subtitle=f"{pr['sku']} · {pr['category']}", status=row["status"] if row else None,
                   facts=[("Stock (all warehouses)", f"{row['qty']:,} {pr['unit']}"), ("Days left", f"{row['days_left']:.0f}" if row and row["days_left"] < 900 else "—"), ("Daily demand", f"{row['daily_demand']:.1f} {pr['unit']}" if row else "—"),
                          ("List price", f"${pr['unit_price']:,.2f}"), ("Revenue, last 12 mo", usd(rev)), ("Cheapest supplier", f"{offers[0]['supplier']} (${offers[0]['unit_price']:,.2f})" if offers else "—")],
                   table={"columns": [{"key": "name", "label": "Warehouse"}, {"key": "qty", "label": "Stock", "fmt": "int"}, {"key": "reorder_level", "label": "Reorder level", "fmt": "int"}], "rows": wh},
                   sources=[src("Open Inventory", "inventory", {"search": pr["name"]})])
        yield msg(f"**{pr['name']}** ({pr['sku']}): **{row['qty']:,} {pr['unit']}** in stock across 4 warehouses" + (f", about **{row['days_left']:.0f} days** of cover" if row and row["days_left"] < 900 else "")
                  + f" — status **{row['status'].replace('_', ' ')}**." + (f" Cheapest supplier: {offers[0]['supplier']} at ${offers[0]['unit_price']:,.2f}." if offers else ""))
        return
    if p.ents.get("customer"):
        c = db.q1("SELECT * FROM customers WHERE id=?", (p.ents["customer"][0].id,))
        per = P.preset("last_12_months", run.as_of)
        agg = db.q1("""SELECT COUNT(DISTINCT o.id) n, SUM(i.qty*i.unit_price) rev, MAX(o.order_date) last FROM sales_orders o JOIN so_items i ON i.order_id=o.id
                       WHERE o.customer_id=? AND o.status!='cancelled' AND o.order_date BETWEEN ? AND ?""", (c["id"], per.start.isoformat(), per.end.isoformat()))
        owed = db.scalar("SELECT SUM(amount) FROM invoices WHERE customer_id=? AND paid_at IS NULL", (c["id"],))
        yield nav("sales", {"search": c["name"], "date_range": "last_24_months"}, label=c["name"])
        yield card("entity", title=c["name"], subtitle=f"Customer · {c['segment']} · {c['region']}", status=None,
                   facts=[("Orders, last 12 mo", f"{agg['n']:,}"), ("Revenue, last 12 mo", usd(agg["rev"])), ("Last order", agg["last"] or "—"), ("Outstanding invoices", usd(owed))],
                   sources=[src("Open Sales", "sales", {"search": c["name"], "date_range": "last_24_months"})])
        yield msg(f"**{c['name']}** ({c['segment']}, {c['region']}): {agg['n']:,} orders worth {usd(agg['rev'])} in the last 12 months; last order {agg['last']}; {usd(owed)} outstanding.")
        return
    yield msg("Which supplier, product, customer, PO or order do you mean? Give me a name or an ID like **PO-2026-0412** or **SO-100234**.")
    run.status = "clarify"


# ----------------------------------------------------------------------------------------------------- generate_report
@handler("generate_report")
def h_report(run):
    p = run.p
    per, defaulted = pick_period(run, "last_month")
    prev = P.previous(per, run.as_of)
    yield step("r", "Collecting KPIs…")
    t = time.perf_counter()
    kp = []
    for m in ("revenue", "orders", "avg_order_value", "profit", "on_time_rate", "cancel_rate"):
        v, pv = run.timed("metrics.value", A.value, m, per.start, per.end), A.value(m, prev.start, prev.end)
        kp.append({"metric": m, "label": A.METRICS[m]["label"], "value": v, "fmt": A.METRICS[m]["fmt"], "change": A.pct_change(v, pv), "good": A.METRICS[m]["good"]})
    yield step("r", "KPIs collected", "done", round((time.perf_counter() - t) * 1000))
    yield step("r2", "Finding top performers and risks…")
    t = time.perf_counter()
    top_p = A.series("revenue", per.start, per.end, "product")[:3]
    top_r = A.series("revenue", per.start, per.end, "region")[:3]
    risk14 = A.stockout_forecast(14)
    pend = db.q1("SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM approvals WHERE status='pending'")
    late = db.scalar("SELECT COUNT(*) FROM shipments WHERE status='delayed'")
    overdue = db.scalar("SELECT SUM(amount) FROM invoices WHERE paid_at IS NULL AND due_at<?", (run.as_of.isoformat(),))
    yield step("r2", "Risks assessed", "done", round((time.perf_counter() - t) * 1000))
    risks, recs = [], []
    if risk14:
        risks.append(f"{len(risk14)} products projected to run out within 14 days ({', '.join(r['name'] for r in risk14[:3])}).")
        recs.append("Create purchase order drafts for the at-risk products.")
    if pend["n"]:
        risks.append(f"{pend['n']} approvals waiting ({usd(pend['v'])}).")
        recs.append("Clear the approval queue so replenishment is not blocked.")
    if late:
        risks.append(f"{late} shipments are past their ETA and still undelivered.")
    if overdue:
        risks.append(f"{usd(overdue)} of customer invoices are overdue.")
        recs.append("Chase overdue receivables.")
    rev = next(k for k in kp if k["metric"] == "revenue")
    if (rev["change"] or 0) < -0.05:
        recs.append(f"Investigate the revenue decline ({pct_txt(rev['change'])}) — ask me “why did revenue change in {per.label}?”.")
    nf = {"date_range": P.preset_id_for(per, run.as_of)}
    yield nav("dashboard", nf, label=per.label)
    yield card("report", title=f"Business report — {per.label}", compare_label=f"vs {prev.label}", kpis=kp,
               top_products=[{"name": r["key"], "value": r["value"]} for r in top_p], top_regions=[{"name": r["key"], "value": r["value"]} for r in top_r], risks=risks, recommendations=recs,
               sources=[src("Open Dashboard", "dashboard", nf)])
    yield msg(f"**Business report — {per.label}** (vs {prev.label})\n" + "\n".join(f"- {k['label']}: **{fmt_value(k['value'], k['fmt'])}** ({pct_txt(k['change'])})" for k in kp)
              + "\n**Top products:** " + ", ".join(f"{r['key']} ({usd(r['value'], True)})" for r in top_p) + "\n**Top regions:** " + ", ".join(f"{r['key']} ({usd(r['value'], True)})" for r in top_r)
              + ("\n**Risks:**\n" + "\n".join("- " + r for r in risks) if risks else "") + ("\n**Suggested next steps:**\n" + "\n".join("- " + r for r in recs) if recs else "")
              + ("\n_No period given, so I used the last full month._" if defaulted else ""))
