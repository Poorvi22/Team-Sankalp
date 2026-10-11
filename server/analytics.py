"""Metric engine used by dashboards, the Analytics page and the agent.

Every number the agent says comes from here, so each statement can be traced to a query and linked to the page that
shows it ("explanation faithfulness"). Results are memoised per data-version, so repeated questions are ~free and
any write invalidates the cache.
"""
import statistics
from datetime import date, timedelta

from . import db, periods as P

METRICS = {
    "revenue": dict(additive=True, label="Revenue", fmt="usd", page="sales", good="up", dims=["month", "day", "category", "region", "product", "customer", "warehouse"]),
    "orders": dict(additive=True, label="Orders", fmt="int", page="sales", good="up", dims=["month", "day", "category", "region", "product", "customer", "warehouse"]),
    "units": dict(additive=True, label="Units sold", fmt="int", page="sales", good="up", dims=["month", "day", "category", "region", "product", "customer", "warehouse"]),
    "avg_order_value": dict(label="Avg order value", fmt="usd", page="sales", good="up", dims=["month", "category", "region", "product"]),
    "cancelled_orders": dict(additive=True, label="Cancelled orders", fmt="int", page="sales", good="down", dims=["month", "category", "region", "product"]),
    "cancel_rate": dict(label="Cancellation rate", fmt="pct", page="sales", good="down", dims=["month", "category", "region"]),
    "on_time_rate": dict(label="On-time delivery rate", fmt="pct", page="logistics", good="up", dims=["month", "region", "warehouse"]),
    "delayed_shipments": dict(additive=True, label="Delayed shipments", fmt="int", page="logistics", good="down", dims=["month", "region", "carrier"]),
    "expenses": dict(additive=True, label="Expenses", fmt="usd", page="finance", good="down", dims=["month", "expense_category"]),
    "profit": dict(additive=True, label="Operating profit", fmt="usd", page="finance", good="up", dims=["month"]),
    "po_spend": dict(additive=True, label="Procurement spend", fmt="usd", page="procurement", good="down", dims=["month", "supplier", "category"]),
    "inventory_value": dict(label="Inventory value", fmt="usd", page="inventory", good="up", dims=["category", "warehouse"], snapshot=True),
    "active_suppliers": dict(label="Active suppliers", fmt="int", page="suppliers", good="up", dims=[], snapshot=True),
    "receivables": dict(label="Outstanding receivables", fmt="usd", page="finance", good="down", dims=["customer"], snapshot=True),
}
SALES_METRICS = {"revenue", "orders", "units", "avg_order_value", "cancelled_orders", "cancel_rate"}

_cache: dict = {}


def memo(fn):
    def wrapper(*a, **k):
        key = (fn.__name__, repr(a), repr(sorted(k.items())), db.data_version())
        hit = _cache.get(key)
        if hit is None:
            hit = fn(*a, **k)
            if len(_cache) > 2000:
                _cache.clear()
            _cache[key] = hit
        return hit
    wrapper.__name__ = fn.__name__
    return wrapper


def iso(d):
    return d.isoformat() if isinstance(d, date) else d


def month_label(k: str, short=False) -> str:
    y, m = k.split("-")
    import calendar
    return f"{calendar.month_abbr[int(m)]}" if short else f"{calendar.month_abbr[int(m)]} {y}"


# ------------------------------------------------------------------------------------------------------ sales family
def _sales_filters(f):
    w, p, joins = [], [], ""
    if f.get("region"):
        w.append("o.region=?"); p.append(f["region"])
    if f.get("category"):
        w.append("pr.category=?"); p.append(f["category"])
    if f.get("product_ids"):
        ids = list(f["product_ids"])
        w.append(f"pr.id IN ({','.join('?' * len(ids))})"); p += ids
    if f.get("customer_id"):
        w.append("o.customer_id=?"); p.append(f["customer_id"])
    if f.get("warehouse_id"):
        w.append("o.warehouse_id=?"); p.append(f["warehouse_id"])
    if f.get("status"):
        w.append("o.status=?"); p.append(f["status"])
    return w, p


_GROUP_SQL = {"month": "substr(o.order_date,1,7)", "day": "o.order_date", "category": "pr.category", "region": "o.region",
              "product": "pr.name", "customer": "c.name", "warehouse": "wh.name"}


@memo
def _sales_rows(start, end, group_by, fkey):
    f = dict(fkey)
    w, p = _sales_filters(f)
    g = _GROUP_SQL[group_by]
    sql = f"""SELECT {g} k,
        COALESCE(SUM(CASE WHEN o.status!='cancelled' THEN i.qty*i.unit_price END),0) revenue,
        COALESCE(SUM(CASE WHEN o.status!='cancelled' THEN i.qty END),0) units,
        COUNT(DISTINCT CASE WHEN o.status!='cancelled' THEN o.id END) orders,
        COUNT(DISTINCT CASE WHEN o.status='cancelled' THEN o.id END) cancelled,
        COUNT(DISTINCT o.id) all_orders
      FROM sales_orders o JOIN so_items i ON i.order_id=o.id JOIN products pr ON pr.id=i.product_id
      JOIN customers c ON c.id=o.customer_id JOIN warehouses wh ON wh.id=o.warehouse_id
      WHERE o.order_date BETWEEN ? AND ? {'AND ' + ' AND '.join(w) if w else ''}
      GROUP BY 1 ORDER BY 1"""
    return db.q(sql, [iso(start), iso(end)] + p)


def _sales_measure(row, metric):
    if metric == "revenue":
        return row["revenue"]
    if metric == "units":
        return row["units"]
    if metric == "orders":
        return row["orders"]
    if metric == "cancelled_orders":
        return row["cancelled"]
    if metric == "cancel_rate":
        return 100.0 * row["cancelled"] / row["all_orders"] if row["all_orders"] else 0.0
    if metric == "avg_order_value":
        return row["revenue"] / row["orders"] if row["orders"] else 0.0
    raise KeyError(metric)


def _fkey(f):
    f = dict(f or {})
    if "product_ids" in f and f["product_ids"] is not None:
        f["product_ids"] = tuple(sorted(f["product_ids"]))
    return tuple(sorted((k, v) for k, v in f.items() if v not in (None, "", [])))


# -------------------------------------------------------------------------------------------------------- public API
def series(metric: str, start: date, end: date, group_by="month", filters=None):
    """-> [{key, label, value}] ordered by key (months are zero-filled)."""
    filters = filters or {}
    key = _fkey(filters)
    if metric in SALES_METRICS:
        rows = {r["k"]: _sales_measure(r, metric) for r in _sales_rows(iso(start), iso(end), group_by, key)}
    elif metric == "profit":
        rev = {r["k"]: r["revenue"] for r in _sales_rows(iso(start), iso(end), "month", key)}
        exp = {r["key"]: r["value"] for r in series("expenses", start, end, "month", filters)}
        rows = {k: rev.get(k, 0) - exp.get(k, 0) for k in set(rev) | set(exp)}
    elif metric == "expenses":
        col = "substr(date,1,7)" if group_by == "month" else "category"
        w = "AND category=?" if filters.get("expense_category") else ""
        p = [iso(start), iso(end)] + ([filters["expense_category"]] if w else [])
        rows = {r["k"]: r["v"] for r in db.q(f"SELECT {col} k, SUM(amount) v FROM expenses WHERE date BETWEEN ? AND ? {w} GROUP BY 1", p)}
    elif metric == "on_time_rate":
        col = {"month": "substr(order_date,1,7)", "region": "region", "warehouse": "warehouse_id"}[group_by]
        rows = {r["k"]: 100.0 * r["ot"] / r["n"] for r in db.q(
            f"""SELECT {col} k, COUNT(*) n, SUM(delivered_date<=promised_date) ot FROM sales_orders
                WHERE status='delivered' AND order_date BETWEEN ? AND ? GROUP BY 1""", [iso(start), iso(end)])}
    elif metric == "delayed_shipments":
        col = {"month": "substr(o.order_date,1,7)", "region": "o.region", "carrier": "s.carrier"}[group_by]
        rows = {r["k"]: r["n"] for r in db.q(
            f"""SELECT {col} k, COUNT(*) n FROM shipments s JOIN sales_orders o ON o.id=s.order_id
                WHERE s.delay_days>0 AND o.order_date BETWEEN ? AND ? GROUP BY 1""", [iso(start), iso(end)])}
    elif metric == "po_spend":
        col = {"month": "substr(po.created_at,1,7)", "supplier": "s.name", "category": "s.category"}[group_by]
        rows = {r["k"]: r["v"] for r in db.q(
            f"""SELECT {col} k, SUM(po.total) v FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
                WHERE po.status IN ('approved','ordered','received') AND po.created_at BETWEEN ? AND ? GROUP BY 1""",
            [iso(start), iso(end)])}
    elif metric == "inventory_value":
        col = {"category": "p.category", "warehouse": "w.name"}.get(group_by, "'All'")
        rows = {r["k"]: r["v"] for r in db.q(
            f"SELECT {col} k, SUM(i.qty*p.unit_cost) v FROM inventory i JOIN products p ON p.id=i.product_id "
            f"JOIN warehouses w ON w.id=i.warehouse_id GROUP BY 1")}
    elif metric == "active_suppliers":
        rows = {"All": db.scalar("SELECT COUNT(*) FROM suppliers WHERE status='active'")}
    elif metric == "receivables":
        col = "c.name" if group_by == "customer" else "'All'"
        rows = {r["k"]: r["v"] for r in db.q(
            f"SELECT {col} k, SUM(v.amount) v FROM invoices v JOIN customers c ON c.id=v.customer_id WHERE v.paid_at IS NULL GROUP BY 1")}
    else:
        raise KeyError(metric)
    if group_by == "month" and not METRICS[metric].get("snapshot"):
        keys = P.month_keys(start, end)
        last_partial = end != P._month_end(end)          # the final bucket is month-to-date
        return [{"key": k, "label": month_label(k), "value": round(rows.get(k, 0.0), 2), **({"partial": True} if last_partial and i == len(keys) - 1 else {})}
                for i, k in enumerate(keys)]
    if group_by == "day":
        out, d = [], start
        while d <= end:
            out.append({"key": d.isoformat(), "label": d.strftime("%d %b"), "value": round(rows.get(d.isoformat(), 0.0), 2)})
            d += timedelta(days=1)
        return out
    return [{"key": k, "label": str(k), "value": round(v, 2)} for k, v in sorted(rows.items(), key=lambda kv: -kv[1])]


def value(metric: str, start: date, end: date, filters=None) -> float:
    """Single number for the whole period."""
    filters = filters or {}
    if metric in SALES_METRICS:
        rows = _sales_rows(iso(start), iso(end), "month", _fkey(filters))
        tot = {k: sum(r[k] for r in rows) for k in ("revenue", "units", "orders", "cancelled", "all_orders")}
        # orders spanning a month boundary cannot happen (order_date is single-valued), so sums are exact
        return round(_sales_measure(tot, metric), 2)
    if metric == "profit":
        return round(value("revenue", start, end, filters) - value("expenses", start, end, filters), 2)
    if metric == "on_time_rate":
        r = db.q1("SELECT COUNT(*) n, SUM(delivered_date<=promised_date) ot FROM sales_orders WHERE status='delivered' "
                  "AND order_date BETWEEN ? AND ?", [iso(start), iso(end)])
        return round(100.0 * (r["ot"] or 0) / r["n"], 1) if r["n"] else 0.0
    return round(sum(x["value"] for x in series(metric, start, end, "month" if not METRICS[metric].get("snapshot") else "none", filters)), 2)


def pct_change(a: float, b: float):
    if not b:
        return None
    return (a - b) / abs(b)


# ------------------------------------------------------------------------------------------------ trend & comparison
def trend_stats(points: list[dict], additive: bool = False) -> dict:
    """Statistics over complete buckets only — a month-to-date bucket would otherwise look like a collapse."""
    partial = [p for p in points if p.get("partial")]
    points = [p for p in points if not p.get("partial")]
    vals = [p["value"] for p in points]
    n = len(vals)
    if n < 2:
        return {"n": n}
    xs = list(range(n))
    mx, my = statistics.mean(xs), statistics.mean(vals)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / sxx if sxx else 0
    half = n // 2
    first, second = statistics.mean(vals[:half]), statistics.mean(vals[half:])
    peak, trough = max(points, key=lambda p: p["value"]), min(points, key=lambda p: p["value"])
    mu = statistics.mean(vals)
    sd = statistics.pstdev(vals) or 1
    anomalies = [p for p in points if abs(p["value"] - mu) / sd > 1.7]
    # month-on-month moves, largest drop / rise
    # month-on-month moves; additive metrics are compared per day so a 28-day February is not "worse" than a 31-day month
    if additive and all(len(str(p["key"])) == 7 for p in points):
        import calendar
        per_day = [v / calendar.monthrange(int(p["key"][:4]), int(p["key"][5:]))[1] for p, v in zip(points, vals)]
    else:
        per_day = vals
    moves = [(points[i], pct_change(per_day[i], per_day[i - 1])) for i in range(1, n) if per_day[i - 1]]
    worst = min(moves, key=lambda m: m[1]) if moves else None
    best = max(moves, key=lambda m: m[1]) if moves else None
    return {"n": n, "partial": partial[0] if partial else None, "total": sum(vals), "mean": mu, "slope": slope, "slope_pct": slope / mu if mu else 0,
            "half_change": pct_change(second, first), "peak": peak, "trough": trough, "anomalies": anomalies,
            "last": points[-1], "prev": points[-2], "mom_last": pct_change(per_day[-1], per_day[-2]), "worst_move": worst,
            "best_move": best}


@memo
def drivers(metric: str, a_start, a_end, b_start, b_end, dim: str, filters_key=()):
    """Contribution of each member of `dim` to the change in `metric` between period A (current) and B (previous)."""
    f = dict(filters_key)
    A = {r["key"]: r["value"] for r in series(metric if metric != "profit" else "revenue", P.date.fromisoformat(a_start),
                                              P.date.fromisoformat(a_end), dim, f)}
    B = {r["key"]: r["value"] for r in series(metric if metric != "profit" else "revenue", P.date.fromisoformat(b_start),
                                              P.date.fromisoformat(b_end), dim, f)}
    keys = set(A) | set(B)
    total_delta = sum(A.get(k, 0) - B.get(k, 0) for k in keys)
    out = []
    for k in keys:
        d = A.get(k, 0) - B.get(k, 0)
        out.append({"name": k, "a": A.get(k, 0), "b": B.get(k, 0), "delta": d, "pct": pct_change(A.get(k, 0), B.get(k, 0)),
                    "share": d / total_delta if total_delta else 0})
    out.sort(key=lambda r: r["delta"])
    return out


def compare(metric: str, pa: P.Period, pb: P.Period, filters=None, with_drivers=True) -> dict:
    filters = filters or {}
    va, vb = value(metric, pa.start, pa.end, filters), value(metric, pb.start, pb.end, filters)
    info = METRICS[metric]
    res = {"metric": metric, "label": info["label"], "fmt": info["fmt"], "a": {"label": pa.label, "value": va, "start": iso(pa.start), "end": iso(pa.end)},
           "b": {"label": pb.label, "value": vb, "start": iso(pb.start), "end": iso(pb.end)},
           "delta": va - vb, "pct": pct_change(va, vb), "good": info["good"]}
    if info["fmt"] in ("usd", "int") and abs(pa.days - pb.days) / max(pa.days, pb.days) > 0.08:
        res["per_day_pct"] = pct_change(va / pa.days, vb / pb.days)
    if with_drivers:
        dims = []
        if metric in SALES_METRICS:
            dims = [d for d in ("category", "region", "product") if d not in ("category",) or not filters.get("category")]
            dims = [d for d in dims if not (d == "region" and filters.get("region"))]
        elif metric == "profit":
            dims = ["category", "region"]
        elif metric == "expenses":
            dims = ["expense_category"]
        elif metric == "on_time_rate":
            dims = ["region"]
        elif metric == "po_spend":
            dims = ["supplier", "category"]
        res["drivers"] = {d: drivers(metric, iso(pa.start), iso(pa.end), iso(pb.start), iso(pb.end), d, _fkey(filters))
                          for d in dims}
    return res


@memo
def supplier_lateness(start, end):
    """Average days late for received inbound POs that were due in the window, per supplier (needs >=2 POs)."""
    return db.q("""SELECT s.id, s.name, s.category, COUNT(*) n,
                   AVG(julianday(po.received_at)-julianday(po.expected_at)) avg_late
                   FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
                   WHERE po.status='received' AND po.expected_at BETWEEN ? AND ?
                   GROUP BY s.id HAVING n>=2 ORDER BY avg_late DESC""", [start, end])


def diagnose(metric: str, pa: P.Period, pb: P.Period, filters=None) -> dict:
    """Evidence-backed explanation of a change. Every finding carries the view that supports it."""
    cmp_ = compare(metric, pa, pb, filters)
    findings = []
    sales_dim_pages = {"category": "sales", "region": "sales", "product": "sales"}
    direction = -1 if (cmp_["delta"] or 0) < 0 else 1
    for dim, rows in cmp_.get("drivers", {}).items():
        if not rows:
            continue
        pool = [r for r in rows if r["delta"] * direction > 0]
        pool.sort(key=lambda r: -abs(r["delta"]))
        for r in pool[:2 if dim != "product" else 3]:
            if abs(r["delta"]) < 1e-9:
                continue
            findings.append({"kind": "driver", "dim": dim, "name": r["name"], "delta": r["delta"], "pct": r["pct"], "a": r["a"],
                             "b": r["b"], "source": {"page": sales_dim_pages.get(dim, "analytics"),
                                                     "filters": ({dim: r["name"]} if dim in ("category", "region") else {"search": r["name"]}) |
                                                                {"date_range": pa.value()}}})
    if metric in SALES_METRICS or metric in ("profit", "on_time_rate"):
        ca, cb = value("cancel_rate", pa.start, pa.end), value("cancel_rate", pb.start, pb.end)
        if abs(ca - cb) >= 2.0:
            findings.append({"kind": "cancellations", "a": ca, "b": cb, "source": {"page": "sales", "filters": {"status": "cancelled", "date_range": pa.value()}}})
        oa, ob = value("on_time_rate", pa.start, pa.end), value("on_time_rate", pb.start, pb.end)
        if abs(oa - ob) >= 3.0:
            findings.append({"kind": "on_time", "a": oa, "b": ob, "source": {"page": "logistics", "filters": {"date_range": pa.value()}}})
        da, db_ = value("delayed_shipments", pa.start, pa.end), value("delayed_shipments", pb.start, pb.end)
        if db_ and (da - db_) / db_ > 0.4 and da - db_ >= 15:
            findings.append({"kind": "delays", "a": da, "b": db_, "source": {"page": "logistics", "filters": {"status": "delayed", "date_range": pa.value()}}})
        la = {r["id"]: r for r in supplier_lateness(iso(pa.start), iso(pa.end))}
        lb = {r["id"]: r for r in supplier_lateness(iso(pb.start), iso(pb.end))}
        for sid, r in sorted(la.items(), key=lambda kv: -kv[1]["avg_late"])[:2]:
            base = lb.get(sid, {}).get("avg_late", 0.0)
            if r["avg_late"] >= 4 and r["avg_late"] - base >= 3:
                findings.append({"kind": "supplier_late", "name": r["name"], "category": r["category"], "a": r["avg_late"], "b": base,
                                 "n": r["n"], "source": {"page": "procurement", "filters": {"supplier": r["name"], "date_range": pa.value()}}})
        xa = value("expenses", pa.start, pa.end)
        xb = value("expenses", pb.start, pb.end)
        if metric == "profit" and xb and abs(xa - xb) / xb > 0.1:
            findings.append({"kind": "expenses", "a": xa, "b": xb, "source": {"page": "finance", "filters": {"date_range": pa.value()}}})
    if metric == "revenue":
        ua, ub = value("units", pa.start, pa.end, filters), value("units", pb.start, pb.end, filters)
        ra, rb = cmp_["a"]["value"], cmp_["b"]["value"]
        if ua and ub and ra and rb:
            findings.append({"kind": "mix", "units_pct": pct_change(ua, ub), "price_pct": pct_change(ra / ua, rb / ub),
                             "source": {"page": "analytics", "filters": {"metric": "units", "date_range": pa.value()}}})
    if metric == "expenses":
        pass
    return {"compare": cmp_, "findings": findings}


# ---------------------------------------------------------------------------------------------------- inventory side
@memo
def _demand(wid, as_of_s):
    since = (date.fromisoformat(as_of_s) - timedelta(days=59)).isoformat()
    w = "AND o.warehouse_id=?" if wid else ""
    rows = db.q(f"""SELECT i.product_id pid, SUM(i.qty)/60.0 d FROM so_items i JOIN sales_orders o ON o.id=i.order_id
                    WHERE o.order_date>=? AND o.status!='cancelled' {w} GROUP BY 1""", [since] + ([wid] if wid else []))
    return {r["pid"]: r["d"] for r in rows}


@memo
def stock_rows(warehouse_id=None, as_of_s=None):
    as_of_s = as_of_s or db.as_of().isoformat()
    w = "WHERE inv.warehouse_id=?" if warehouse_id else ""
    rows = db.q(f"""SELECT p.id pid, p.sku, p.name, p.category, p.unit, p.unit_cost, p.supplier_id, s.name supplier,
                    SUM(inv.qty) qty, SUM(inv.reorder_level) reorder_level
                    FROM inventory inv JOIN products p ON p.id=inv.product_id LEFT JOIN suppliers s ON s.id=p.supplier_id
                    {w} GROUP BY p.id""", [warehouse_id] if warehouse_id else [])
    dem = _demand(warehouse_id, as_of_s)
    for r in rows:
        d = dem.get(r["pid"], 0.0)
        r["daily_demand"] = round(d, 2)
        r["days_left"] = round(r["qty"] / d, 1) if d > 0 else 999.0
        r["status"] = "out_of_stock" if r["qty"] <= 0 else ("low_stock" if r["qty"] <= r["reorder_level"] else "in_stock")
        r["value"] = round(r["qty"] * r["unit_cost"], 2)
    return rows


def stockout_forecast(horizon_days: int, warehouse_id=None):
    """Products whose current stock covers fewer than `horizon_days` of recent demand (60-day average)."""
    rows = [r for r in stock_rows(warehouse_id) if r["days_left"] <= horizon_days]
    rows.sort(key=lambda r: r["days_left"])
    return rows


@memo
def supplier_offers(product_ids: tuple):
    ids = list(product_ids)
    return db.q(f"""SELECT sp.supplier_id, sp.product_id, sp.unit_price, sp.min_qty, sp.lead_time_days, s.name supplier, s.rating,
                    s.on_time_pct, s.country, s.payment_terms, p.name product, p.sku, p.unit
                    FROM supplier_prices sp JOIN suppliers s ON s.id=sp.supplier_id JOIN products p ON p.id=sp.product_id
                    WHERE sp.product_id IN ({','.join('?' * len(ids))}) AND s.status='active'""", ids)


WEIGHTS = {"price": (0.60, 0.15, 0.15, 0.10), "lead_time": (0.20, 0.55, 0.15, 0.10),
           "reliability": (0.15, 0.10, 0.50, 0.25), "balanced": (0.40, 0.20, 0.25, 0.15)}


def rank_suppliers(product_ids, criterion="balanced"):
    """Score suppliers over the products they offer: price index, lead time, on-time %, rating (0-100, higher is better)."""
    offers = supplier_offers(tuple(sorted(product_ids)))
    if not offers:
        return []
    best_price = {}
    for o in offers:
        best_price[o["product_id"]] = min(best_price.get(o["product_id"], 1e18), o["unit_price"])
    by_sup = {}
    for o in offers:
        s = by_sup.setdefault(o["supplier_id"], {"supplier_id": o["supplier_id"], "supplier": o["supplier"], "country": o["country"],
                                                 "rating": o["rating"], "on_time_pct": o["on_time_pct"], "items": [], "payment_terms": o["payment_terms"]})
        s["items"].append(o)
    max_lead = max(o["lead_time_days"] for o in offers) or 1
    wp, wl, wo, wr = WEIGHTS.get(criterion, WEIGHTS["balanced"])
    out = []
    for s in by_sup.values():
        idx = statistics.mean(it["unit_price"] / best_price[it["product_id"]] for it in s["items"])   # 1.0 = cheapest
        lead = statistics.mean(it["lead_time_days"] for it in s["items"])
        price_score = max(0.0, 100 - (idx - 1) * 250)
        lead_score = 100 * (1 - lead / (max_lead * 1.2))
        score = wp * price_score + wl * lead_score + wo * s["on_time_pct"] + wr * (s["rating"] / 5 * 100)
        s.update(price_index=round(idx, 3), avg_lead=round(lead, 1), score=round(score, 1), n_products=len(s["items"]))
        out.append(s)
    # a supplier covering more of the requested products is more useful than one covering a single product
    top_cov = max(s["n_products"] for s in out)
    out.sort(key=lambda s: -(s["score"] + 6 * (s["n_products"] / top_cov)))
    return out
