"""Application metadata + page builders: the single source of truth for the UI *and* the agent.

Each page declares an id, route, description, filters (the UI state the agent may set) and widgets (what is rendered).
The browser renders pages generically from this metadata, and the agent's knowledge of "what exists and what it can
change" is generated from the very same structures — so the two cannot drift apart.
"""
from datetime import timedelta

from . import analytics as A, db, periods as P

STATUS_COLORS = {}

# ----------------------------------------------------------------------------------------------------- option lists
def opt(v, l=None):
    return {"value": v, "label": l or v}


def options():
    cats = [r["category"] for r in db.q("SELECT DISTINCT category FROM products ORDER BY 1")]
    return {
        "categories": [opt(c) for c in cats],
        "regions": [opt(r) for r in ("North America", "Europe", "APAC", "LATAM")],
        "warehouses": [opt(str(r["id"]), r["name"]) for r in db.q("SELECT id,name FROM warehouses ORDER BY id")],
        "suppliers": [opt(r["name"]) for r in db.q("SELECT name FROM suppliers ORDER BY name")],
        "countries": [opt(r["country"]) for r in db.q("SELECT DISTINCT country FROM suppliers ORDER BY 1")],
        "carriers": [opt(r["carrier"]) for r in db.q("SELECT DISTINCT carrier FROM shipments ORDER BY 1")],
        "expense_categories": [opt(r["category"]) for r in db.q("SELECT DISTINCT category FROM expenses ORDER BY 1")],
    }


def _dr():
    return {"key": "date_range", "label": "Date range", "type": "date_range", "default": "last_12_months",
            "description": "Restricts the data to a period. Accepts a preset (last_12_months, last_quarter, ytd...) or an explicit start..end range."}


SORT = lambda opts: {"key": "sort", "label": "Sort by", "type": "select", "options": [opt(f"{k}:{d}", l) for k, d, l in opts],
                     "description": "Sort order of the main table."}

# --------------------------------------------------------------------------------------------------------- page defs
PAGES: dict = {}


def page(pid, title, icon, group, description, keywords, filters, widgets, actions=None):
    def deco(fn):
        PAGES[pid] = dict(id=pid, title=title, icon=icon, group=group, description=description, keywords=keywords,
                          filters=filters, widgets=widgets, actions=actions or [], build=fn)
        return fn
    return deco


def W(wid, wtype, title, desc, span=12):
    return dict(id=wid, type=wtype, title=title, description=desc, span=span)


def _range(f):
    p = P.resolve(f.get("date_range"), db.as_of())
    return p, P.previous(p, db.as_of())


def _sort(f, allowed, default):
    s = (f.get("sort") or default)
    k, _, d = s.partition(":")
    return (k if k in allowed else default.split(":")[0]), ("DESC" if d == "desc" else "ASC")


def kpi(label, value, fmt, delta=None, good="up", icon="chart", sub=None):
    return {"label": label, "value": value, "fmt": fmt, "delta": delta, "good": good, "icon": icon, "sub": sub}


# ------------------------------------------------------------------------------------------------------- dashboard
@page("dashboard", "Dashboard", "grid", "Overview",
      "Executive overview of the business: revenue, orders, inventory value, suppliers, revenue and order trend, inventory health, "
      "low-stock alerts, recent purchase orders, fulfilment rate, expense breakdown and top-selling products.",
      "home overview executive summary kpi business health snapshot control tower",
      [_dr(), {"key": "warehouse", "label": "Location", "type": "select", "options_from": "warehouses",
               "description": "Warehouse / location the stock widgets are restricted to."}],
      [W("kpi_row", "kpi_row", "Key metrics", "Total revenue, total orders, inventory value and active suppliers with change vs previous period."),
       W("revenue_trend", "line", "Revenue & orders trend", "Monthly revenue trend over the selected date range.", 8),
       W("inventory_status", "donut", "Inventory status", "Share of items that are in stock, low on stock or out of stock.", 4),
       W("low_stock_alerts", "table", "Low stock alerts", "Products that will run out soonest, with current stock, reorder level and days left.", 6),
       W("recent_pos", "table", "Recent purchase orders", "Most recent purchase orders with supplier, status and amount.", 6),
       W("fulfillment", "donut", "Order fulfilment rate", "Share of delivered orders that arrived on or before the promised date.", 4),
       W("expense_breakdown", "donut", "Expense breakdown", "Expenses split by category: raw materials, logistics, manufacturing, administrative, other.", 4),
       W("top_products", "table", "Top selling products", "Best-selling products by revenue with units sold.", 4)])
def build_dashboard(f, user):
    p, prev = _range(f)
    wid = int(f["warehouse"]) if f.get("warehouse") else None
    fl = {"warehouse_id": wid} if wid else {}
    rev, rev_p = A.value("revenue", p.start, p.end, fl), A.value("revenue", prev.start, prev.end, fl)
    od, od_p = A.value("orders", p.start, p.end, fl), A.value("orders", prev.start, prev.end, fl)
    stock = A.stock_rows(wid)
    inv_val = sum(r["value"] for r in stock)
    ser = A.series("revenue", p.start, p.end, "month", fl)
    ser_o = A.series("orders", p.start, p.end, "month", fl)
    counts = {s: sum(1 for r in stock if r["status"] == s) for s in ("in_stock", "low_stock", "out_of_stock")}
    low = sorted([r for r in stock if r["status"] != "in_stock"], key=lambda r: r["days_left"])[:5]
    pos = db.q("""SELECT po.po_number, s.name supplier, po.status, po.total, po.created_at FROM purchase_orders po
                  JOIN suppliers s ON s.id=po.supplier_id ORDER BY po.created_at DESC, po.id DESC LIMIT 5""")
    ot = A.value("on_time_rate", p.start, p.end)
    exp = A.series("expenses", p.start, p.end, "expense_category")
    top = A.series("revenue", p.start, p.end, "product", fl)[:5]
    units = {r["key"]: r["value"] for r in A.series("units", p.start, p.end, "product", fl)}
    ac = A.value("active_suppliers", p.start, p.end)
    return {
        "kpi_row": {"items": [kpi("Total Revenue", rev, "usd", A.pct_change(rev, rev_p), icon="revenue"),
                              kpi("Total Orders", od, "int", A.pct_change(od, od_p), icon="orders"),
                              kpi("Inventory Value", inv_val, "usd", None, icon="inventory", sub="current stock at cost"),
                              kpi("Active Suppliers", ac, "int", None, icon="suppliers", sub=f"{db.scalar('SELECT COUNT(*) FROM suppliers')} total")],
                    "compare_label": f"vs {prev.label}"},
        "revenue_trend": {"series": [{"name": "Revenue", "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in ser]}], "fmt": "usd",
                          "secondary": {"name": "Orders", "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in ser_o]}},
        "inventory_status": {"slices": [{"label": "In stock", "value": counts["in_stock"], "color": "ok"},
                                        {"label": "Low stock", "value": counts["low_stock"], "color": "warn"},
                                        {"label": "Out of stock", "value": counts["out_of_stock"], "color": "bad"}],
                             "center": {"value": len(stock), "label": "Total items"}, "fmt": "int",
                             "link": {"page": "inventory", "param": "status", "values": ["in_stock", "low_stock", "out_of_stock"]}},
        "low_stock_alerts": {"columns": [{"key": "name", "label": "Product", "bold": True}, {"key": "qty", "label": "Current stock", "fmt": "int"},
                                         {"key": "reorder_level", "label": "Reorder level", "fmt": "int"},
                                         {"key": "days_left", "label": "Days left", "fmt": "days", "badge": "days"}],
                             "rows": low, "link": {"page": "inventory", "filters": {"status": "low_stock", "sort": "days_left:asc"}}},
        "recent_pos": {"columns": [{"key": "po_number", "label": "PO #", "bold": True}, {"key": "supplier", "label": "Supplier"},
                                   {"key": "status", "label": "Status", "badge": "status"}, {"key": "total", "label": "Amount", "fmt": "usd"},
                                   {"key": "created_at", "label": "Date", "fmt": "date"}],
                       "rows": pos, "link": {"page": "procurement", "filters": {}}},
        "fulfillment": {"slices": [{"label": "On time", "value": round(ot, 1), "color": "ok"}, {"label": "Late", "value": round(100 - ot, 1), "color": "bad"}],
                        "center": {"value": f"{ot:.0f}%", "label": "Delivered on time"}, "fmt": "pct"},
        "expense_breakdown": {"slices": [{"label": e["key"], "value": e["value"]} for e in exp],
                              "center": {"value": sum(e["value"] for e in exp), "label": "Total", "fmt": "usd_short"}, "fmt": "usd"},
        "top_products": {"columns": [{"key": "name", "label": "Product", "bold": True}, {"key": "units", "label": "Units sold", "fmt": "int"},
                                     {"key": "revenue", "label": "Revenue", "fmt": "usd"}],
                         "rows": [{"name": t["key"], "units": units.get(t["key"], 0), "revenue": t["value"]} for t in top],
                         "link": {"page": "analytics", "filters": {"metric": "revenue", "group_by": "product"}}},
    }


# ------------------------------------------------------------------------------------------------------- inventory
@page("inventory", "Inventory", "box", "Operations",
      "Stock levels per product across warehouses with reorder levels, days of cover (stock divided by recent daily demand) and stock status. "
      "Use it to find products that are low or out of stock and to create purchase orders or adjust stock.",
      "stock levels warehouse sku reorder low stock out of stock days left cover shortage replenishment",
      [{"key": "status", "label": "Status", "type": "select", "options": [opt("in_stock", "In stock"), opt("low_stock", "Low stock"), opt("out_of_stock", "Out of stock")],
        "description": "Stock status of the product."},
       {"key": "category", "label": "Category", "type": "select", "options_from": "categories", "description": "Product category."},
       {"key": "warehouse", "label": "Warehouse", "type": "select", "options_from": "warehouses", "description": "Warehouse / location."},
       {"key": "search", "label": "Search", "type": "search", "description": "Free text on product name or SKU."},
       SORT([("name", "asc", "Name"), ("days_left", "asc", "Days left (lowest first)"), ("qty", "asc", "Stock (lowest first)"),
             ("qty", "desc", "Stock (highest first)"), ("value", "desc", "Stock value")])],
      [W("kpis", "kpi_row", "Stock summary", "Counts of in-stock, low-stock and out-of-stock products, and total inventory value."),
       W("by_category", "bar", "Stock value by category", "Inventory value at cost per product category.", 5),
       W("stock_table", "table", "Stock levels", "Each product with stock, reorder level, days left, status, supplier and quick actions.", 7)],
      actions=[{"id": "po_new", "label": "+ New purchase order", "form": "po"}])
def build_inventory(f, user):
    wid = int(f["warehouse"]) if f.get("warehouse") else None
    rows = A.stock_rows(wid)
    if f.get("status"):
        rows = [r for r in rows if r["status"] == f["status"]]
    if f.get("category"):
        rows = [r for r in rows if r["category"] == f["category"]]
    if f.get("search"):
        s = f["search"].lower()
        rows = [r for r in rows if s in r["name"].lower() or s in r["sku"].lower()]
    allrows = A.stock_rows(wid)
    k, d = _sort(f, {"name", "days_left", "qty", "value"}, "days_left:asc")
    rows = sorted(rows, key=lambda r: r[k], reverse=(d == "DESC"))
    cat = {}
    for r in allrows:
        cat[r["category"]] = cat.get(r["category"], 0) + r["value"]
    return {
        "kpis": {"items": [kpi("In stock", sum(r["status"] == "in_stock" for r in allrows), "int", icon="ok"),
                           kpi("Low stock", sum(r["status"] == "low_stock" for r in allrows), "int", icon="warn", good="down"),
                           kpi("Out of stock", sum(r["status"] == "out_of_stock" for r in allrows), "int", icon="bad", good="down"),
                           kpi("Inventory value", sum(r["value"] for r in allrows), "usd", icon="inventory")]},
        "by_category": {"bars": [{"label": k_, "value": v} for k_, v in sorted(cat.items(), key=lambda kv: -kv[1])], "fmt": "usd", "horizontal": True},
        "stock_table": {"columns": [{"key": "name", "label": "Product", "bold": True, "sub": "sku"}, {"key": "category", "label": "Category"},
                                    {"key": "qty", "label": "Stock", "fmt": "int", "sort": "qty"}, {"key": "reorder_level", "label": "Reorder level", "fmt": "int"},
                                    {"key": "days_left", "label": "Days left", "fmt": "days", "badge": "days", "sort": "days_left"},
                                    {"key": "status", "label": "Status", "badge": "status"}, {"key": "supplier", "label": "Primary supplier"}],
                        "rows": rows[:100], "total": len(rows),
                        "row_actions": [{"id": "po_for_product", "label": "Order"}, {"id": "edit_stock", "label": "Edit"}], "id_key": "pid"},
    }


# ------------------------------------------------------------------------------------------------------- suppliers
@page("suppliers", "Suppliers", "users", "Operations",
      "Supplier directory with country, category, rating, on-time delivery percentage, lead time and status. Compare vendors and add or "
      "put suppliers on hold.",
      "vendors supplier directory scorecard rating on-time lead time vendor management sourcing",
      [{"key": "country", "label": "Country", "type": "select", "options_from": "countries", "description": "Supplier country."},
       {"key": "category", "label": "Category", "type": "select", "options_from": "categories", "description": "Product category the supplier serves."},
       {"key": "status", "label": "Status", "type": "select", "options": [opt("active", "Active"), opt("on_hold", "On hold"), opt("inactive", "Inactive")],
        "description": "Supplier status."},
       {"key": "rating_min", "label": "Min rating", "type": "select", "options": [opt("4.5", "4.5+"), opt("4", "4.0+"), opt("3.5", "3.5+")],
        "description": "Minimum supplier rating."},
       {"key": "search", "label": "Search", "type": "search", "description": "Supplier name."},
       SORT([("name", "asc", "Name"), ("rating", "desc", "Rating"), ("on_time_pct", "desc", "On-time %"), ("lead_time_days", "asc", "Lead time (shortest)"),
             ("spend", "desc", "Spend")])],
      [W("kpis", "kpi_row", "Supplier summary", "Active suppliers, average rating, average on-time delivery and average lead time."),
       W("scorecard", "bar", "On-time delivery by supplier", "On-time delivery percentage of the best/worst suppliers in the current view.", 5),
       W("supplier_table", "table", "Supplier directory", "All suppliers with country, category, rating, on-time %, lead time, spend and status.", 7)],
      actions=[{"id": "supplier_new", "label": "+ Add supplier", "form": "supplier"}])
def build_suppliers(f, user):
    w, p = [], []
    for key, col in (("country", "s.country"), ("category", "s.category"), ("status", "s.status")):
        if f.get(key):
            w.append(f"{col}=?"); p.append(f[key])
    if f.get("rating_min"):
        w.append("s.rating>=?"); p.append(float(f["rating_min"]))
    if f.get("search"):
        w.append("s.name LIKE ?"); p.append(f"%{f['search']}%")
    k, d = _sort(f, {"name", "rating", "on_time_pct", "lead_time_days", "spend"}, "name:asc")
    rows = db.q(f"""SELECT s.id, s.name, s.country, s.category, s.rating, s.on_time_pct, s.lead_time_days, s.payment_terms, s.status,
                    COALESCE((SELECT SUM(po.total) FROM purchase_orders po WHERE po.supplier_id=s.id AND po.status IN ('approved','ordered','received')),0) spend
                    FROM suppliers s {'WHERE ' + ' AND '.join(w) if w else ''} ORDER BY {k} {d}""", p)
    n = len(rows) or 1
    best = sorted(rows, key=lambda r: -r["on_time_pct"])[:10]
    return {
        "kpis": {"items": [kpi("Suppliers in view", len(rows), "int", icon="suppliers"),
                           kpi("Avg rating", sum(r["rating"] for r in rows) / n, "dec1", icon="star"),
                           kpi("Avg on-time", sum(r["on_time_pct"] for r in rows) / n, "pct", icon="ok"),
                           kpi("Avg lead time", sum(r["lead_time_days"] for r in rows) / n, "days", icon="clock", good="down")]},
        "scorecard": {"bars": [{"label": r["name"], "value": r["on_time_pct"]} for r in best], "fmt": "pct", "horizontal": True, "min": 70},
        "supplier_table": {"columns": [{"key": "name", "label": "Supplier", "bold": True}, {"key": "country", "label": "Country"},
                                       {"key": "category", "label": "Category"}, {"key": "rating", "label": "Rating", "fmt": "dec1", "sort": "rating"},
                                       {"key": "on_time_pct", "label": "On-time", "fmt": "pct", "sort": "on_time_pct"},
                                       {"key": "lead_time_days", "label": "Lead time", "fmt": "days", "sort": "lead_time_days"},
                                       {"key": "spend", "label": "Spend", "fmt": "usd", "sort": "spend"}, {"key": "status", "label": "Status", "badge": "status"}],
                           "rows": rows[:100], "total": len(rows),
                           "row_actions": [{"id": "supplier_hold", "label": "Hold / activate"}], "id_key": "id"},
    }


# ----------------------------------------------------------------------------------------------------- procurement
PO_STATUS = [opt("draft", "Draft"), opt("pending_approval", "Pending approval"), opt("approved", "Approved"), opt("ordered", "Ordered"),
             opt("received", "Received"), opt("cancelled", "Cancelled")]


@page("procurement", "Procurement", "doc", "Operations",
      "Purchase orders to suppliers: create, review and track POs through draft, pending approval, approved, ordered and received. "
      "POs above $50,000 need approval. Delete is only allowed for drafts.",
      "purchase orders po buy order from supplier procurement purchasing replenish draft approval receive",
      [{"key": "status", "label": "Status", "type": "select", "options": PO_STATUS, "description": "Purchase order status."},
       {"key": "supplier", "label": "Supplier", "type": "select", "options_from": "suppliers", "description": "Supplier the PO was placed with."},
       _dr(), {"key": "search", "label": "Search", "type": "search", "description": "PO number."},
       SORT([("created_at", "desc", "Newest"), ("created_at", "asc", "Oldest"), ("total", "desc", "Amount (high to low)"), ("total", "asc", "Amount (low to high)")])],
      [W("kpis", "kpi_row", "Procurement summary", "PO count, total spend, open POs and POs waiting for approval in the current view."),
       W("by_status", "donut", "POs by status", "Distribution of purchase orders per status.", 4),
       W("po_table", "table", "Purchase orders", "Purchase orders with supplier, status, expected date and amount; row actions to receive, approve or delete drafts.", 8)],
      actions=[{"id": "po_new", "label": "+ New purchase order", "form": "po"}])
def build_procurement(f, user):
    p = P.resolve(f.get("date_range"), db.as_of(), default="last_12_months")
    w, prm = ["po.created_at BETWEEN ? AND ?"], [p.start.isoformat(), p.end.isoformat()]
    if f.get("status"):
        w.append("po.status=?"); prm.append(f["status"])
    if f.get("supplier"):
        w.append("s.name=?"); prm.append(f["supplier"])
    if f.get("search"):
        w.append("po.po_number LIKE ?"); prm.append(f"%{f['search']}%")
    k, d = _sort(f, {"created_at", "total"}, "created_at:desc")
    rows = db.q(f"""SELECT po.id, po.po_number, s.name supplier, po.status, po.created_at, po.expected_at, po.total, po.created_by,
                    (SELECT COUNT(*) FROM po_items i WHERE i.po_id=po.id) lines
                    FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE {' AND '.join(w)} ORDER BY po.{k} {d}, po.id DESC""", prm)
    st = {}
    for r in rows:
        st[r["status"]] = st.get(r["status"], 0) + 1
    return {
        "kpis": {"items": [kpi("Purchase orders", len(rows), "int", icon="doc"),
                           kpi("Total value", sum(r["total"] for r in rows if r["status"] != "cancelled"), "usd", icon="revenue"),
                           kpi("Open POs", sum(r["status"] in ("approved", "ordered") for r in rows), "int", icon="clock"),
                           kpi("Awaiting approval", sum(r["status"] == "pending_approval" for r in rows), "int", icon="warn", good="down")]},
        "by_status": {"slices": [{"label": s_, "value": n_} for s_, n_ in st.items()], "center": {"value": len(rows), "label": "POs"}, "fmt": "int", "status_labels": True},
        "po_table": {"columns": [{"key": "po_number", "label": "PO #", "bold": True}, {"key": "supplier", "label": "Supplier"},
                                 {"key": "status", "label": "Status", "badge": "status"}, {"key": "created_at", "label": "Created", "fmt": "date", "sort": "created_at"},
                                 {"key": "expected_at", "label": "Expected", "fmt": "date"}, {"key": "total", "label": "Amount", "fmt": "usd", "sort": "total"}],
                     "rows": rows[:100], "total": len(rows), "id_key": "id",
                     "row_actions": [{"id": "po_approve", "label": "Approve", "when": {"status": "pending_approval"}},
                                     {"id": "po_receive", "label": "Receive", "when": {"status": ["approved", "ordered"]}},
                                     {"id": "po_delete", "label": "Delete", "when": {"status": "draft"}, "danger": True}]},
    }


# --------------------------------------------------------------------------------------------------------- sales
SO_STATUS = [opt("pending", "Pending"), opt("processing", "Processing"), opt("shipped", "Shipped"), opt("delivered", "Delivered"), opt("cancelled", "Cancelled")]


@page("sales", "Sales & Orders", "cart", "Commercial",
      "Customer sales orders with status, region and value; revenue and order trends, orders by status, and the ability to move orders "
      "through processing, shipped, delivered or cancelled.",
      "sales orders customers revenue sold ordering fulfilment cancelled delivered shipped pending region",
      [{"key": "status", "label": "Status", "type": "select", "options": SO_STATUS, "description": "Order status."},
       {"key": "region", "label": "Region", "type": "select", "options_from": "regions", "description": "Customer region."},
       {"key": "category", "label": "Category", "type": "select", "options_from": "categories", "description": "Only orders containing this product category."},
       _dr(), {"key": "search", "label": "Search", "type": "search", "description": "Order number or customer name."},
       SORT([("order_date", "desc", "Newest"), ("order_date", "asc", "Oldest"), ("total", "desc", "Value (high to low)"), ("total", "asc", "Value (low to high)")])],
      [W("kpis", "kpi_row", "Sales summary", "Revenue, orders, average order value and cancellation rate for the selected filters."),
       W("trend", "line", "Revenue trend", "Revenue over time for the selected filters.", 8),
       W("by_status", "donut", "Orders by status", "Distribution of orders per status.", 4),
       W("orders_table", "table", "Orders", "Sales orders with customer, region, value, status and dates; row action to update status.", 12)])
def build_sales(f, user):
    p, prev = _range(f)
    fl = {k: f[k] for k in ("region", "category") if f.get(k)}
    cur = {m: A.value(m, p.start, p.end, fl) for m in ("revenue", "orders", "avg_order_value", "cancel_rate")}
    pre = {m: A.value(m, prev.start, prev.end, fl) for m in ("revenue", "orders", "avg_order_value", "cancel_rate")}
    ser = A.series("revenue", p.start, p.end, "day" if p.days <= 62 else "month", fl)
    w, prm, joins = ["o.order_date BETWEEN ? AND ?"], [p.start.isoformat(), p.end.isoformat()], ""
    if f.get("status"):
        w.append("o.status=?"); prm.append(f["status"])
    if f.get("region"):
        w.append("o.region=?"); prm.append(f["region"])
    if f.get("category"):
        w.append("o.id IN (SELECT i.order_id FROM so_items i JOIN products pr ON pr.id=i.product_id WHERE pr.category=?)"); prm.append(f["category"])
    if f.get("search"):
        w.append("(o.so_number LIKE ? OR c.name LIKE ?)"); prm += [f"%{f['search']}%"] * 2
    k, d = _sort(f, {"order_date", "total"}, "order_date:desc")
    base = f"FROM sales_orders o JOIN customers c ON c.id=o.customer_id WHERE {' AND '.join(w)}"
    rows = db.q(f"SELECT o.id, o.so_number, c.name customer, o.region, o.status, o.order_date, o.promised_date, o.total {base} ORDER BY o.{k} {d}, o.id DESC LIMIT 100", prm)
    total = db.scalar(f"SELECT COUNT(*) {base}", prm)
    st = db.q(f"SELECT o.status k, COUNT(*) n {base} GROUP BY 1", prm)
    return {
        "kpis": {"items": [kpi("Revenue", cur["revenue"], "usd", A.pct_change(cur["revenue"], pre["revenue"]), icon="revenue"),
                           kpi("Orders", cur["orders"], "int", A.pct_change(cur["orders"], pre["orders"]), icon="orders"),
                           kpi("Avg order value", cur["avg_order_value"], "usd", A.pct_change(cur["avg_order_value"], pre["avg_order_value"]), icon="chart"),
                           kpi("Cancellation rate", cur["cancel_rate"], "pct", A.pct_change(cur["cancel_rate"], pre["cancel_rate"]), good="down", icon="warn")],
                 "compare_label": f"vs {prev.label}"},
        "trend": {"series": [{"name": "Revenue", "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in ser]}], "fmt": "usd"},
        "by_status": {"slices": [{"label": s["k"], "value": s["n"]} for s in st], "center": {"value": total, "label": "Orders"}, "fmt": "int", "status_labels": True},
        "orders_table": {"columns": [{"key": "so_number", "label": "Order", "bold": True}, {"key": "customer", "label": "Customer"}, {"key": "region", "label": "Region"},
                                     {"key": "order_date", "label": "Date", "fmt": "date", "sort": "order_date"}, {"key": "total", "label": "Value", "fmt": "usd", "sort": "total"},
                                     {"key": "status", "label": "Status", "badge": "status"}],
                         "rows": rows, "total": total, "id_key": "id", "row_actions": [{"id": "order_status", "label": "Update status"}]},
    }


# ------------------------------------------------------------------------------------------------------- logistics
@page("logistics", "Logistics", "truck", "Operations",
      "Outbound shipments with carrier, route, ETA and delay; on-time delivery performance, delayed shipments and carrier comparison.",
      "shipments delivery tracking carrier freight transit delayed late eta on-time shipping logistics",
      [{"key": "status", "label": "Status", "type": "select", "options": [opt("in_transit", "In transit"), opt("delivered", "Delivered"), opt("delayed", "Delayed")],
        "description": "Shipment status; 'delayed' means past the ETA and still not delivered."},
       {"key": "carrier", "label": "Carrier", "type": "select", "options_from": "carriers", "description": "Carrier."},
       {"key": "region", "label": "Region", "type": "select", "options_from": "regions", "description": "Destination region."},
       _dr(),
       SORT([("shipped_at", "desc", "Newest"), ("delay_days", "desc", "Most delayed")])],
      [W("kpis", "kpi_row", "Delivery performance", "Shipments, on-time rate, delayed shipments and average delay."),
       W("by_carrier", "bar", "On-time rate by carrier", "Share of delivered shipments that arrived by the ETA, per carrier.", 5),
       W("shipments", "table", "Shipments", "Shipments with order, carrier, destination, ETA, delay and status.", 7)])
def build_logistics(f, user):
    p, prev = _range(f)
    w, prm = ["s.shipped_at BETWEEN ? AND ?"], [p.start.isoformat(), p.end.isoformat()]
    for key, col in (("status", "s.status"), ("carrier", "s.carrier"), ("region", "s.destination")):
        if f.get(key):
            w.append(f"{col}=?"); prm.append(f[key])
    k, d = _sort(f, {"shipped_at", "delay_days"}, "shipped_at:desc")
    where = ' AND '.join(w)
    rows = db.q(f"""SELECT s.id, o.so_number, s.carrier, s.destination region, s.origin, s.shipped_at, s.eta, s.delay_days, s.status
                    FROM shipments s JOIN sales_orders o ON o.id=s.order_id WHERE {where} ORDER BY s.{k} {d}, s.id DESC LIMIT 100""", prm)
    agg = db.q1(f"SELECT COUNT(*) n, SUM(s.delay_days>0) late, AVG(CASE WHEN s.delay_days>0 THEN s.delay_days END) avg_delay FROM shipments s WHERE {where}", prm)
    car = db.q("""SELECT carrier k, 100.0*SUM(delay_days=0)/COUNT(*) v FROM shipments WHERE status='delivered' AND shipped_at BETWEEN ? AND ? GROUP BY 1""",
               [p.start.isoformat(), p.end.isoformat()])
    ot = A.value("on_time_rate", p.start, p.end)
    return {
        "kpis": {"items": [kpi("Shipments", agg["n"], "int", icon="truck"), kpi("On-time rate", ot, "pct", A.pct_change(ot, A.value("on_time_rate", prev.start, prev.end)), icon="ok"),
                           kpi("Delayed", agg["late"] or 0, "int", good="down", icon="warn"),
                           kpi("Avg delay (late ones)", agg["avg_delay"] or 0, "days", good="down", icon="clock")], "compare_label": f"vs {prev.label}"},
        "by_carrier": {"bars": [{"label": c["k"], "value": c["v"]} for c in sorted(car, key=lambda c: -c["v"])], "fmt": "pct", "horizontal": True, "min": 70},
        "shipments": {"columns": [{"key": "so_number", "label": "Order", "bold": True}, {"key": "carrier", "label": "Carrier"}, {"key": "origin", "label": "From"},
                                  {"key": "region", "label": "To"}, {"key": "eta", "label": "ETA", "fmt": "date"},
                                  {"key": "delay_days", "label": "Delay", "fmt": "days", "sort": "delay_days"}, {"key": "status", "label": "Status", "badge": "status"}],
                      "rows": rows, "total": agg["n"]},
    }


# --------------------------------------------------------------------------------------------------------- finance
@page("finance", "Finance", "dollar", "Commercial",
      "Revenue versus expenses, operating profit, expense breakdown by category and outstanding customer receivables with aging.",
      "finance revenue expenses profit margin cost receivables invoices cash aging money",
      [_dr(), {"key": "expense_category", "label": "Expense category", "type": "select", "options_from": "expense_categories", "description": "Restrict expenses to one category."}],
      [W("kpis", "kpi_row", "Financial summary", "Revenue, expenses, operating profit and outstanding receivables."),
       W("rev_vs_exp", "line", "Revenue vs expenses", "Monthly revenue and expenses over the selected range.", 8),
       W("expense_mix", "donut", "Expense breakdown", "Expenses by category.", 4),
       W("aging", "bar", "Receivables aging", "Unpaid customer invoices by days past due.", 6),
       W("top_debtors", "table", "Largest outstanding balances", "Customers with the highest unpaid invoice totals.", 6)])
def build_finance(f, user):
    p, prev = _range(f)
    ec = {"expense_category": f["expense_category"]} if f.get("expense_category") else {}
    rev, exp = A.value("revenue", p.start, p.end), A.value("expenses", p.start, p.end, ec)
    rev_p, exp_p = A.value("revenue", prev.start, prev.end), A.value("expenses", prev.start, prev.end, ec)
    r_s, e_s = A.series("revenue", p.start, p.end), A.series("expenses", p.start, p.end, "month", ec)
    mix = A.series("expenses", p.start, p.end, "expense_category")
    as_of = db.as_of().isoformat()
    ag = db.q1("""SELECT SUM(CASE WHEN due_at>=? THEN amount END) current_,
                         SUM(CASE WHEN due_at<? AND julianday(?)-julianday(due_at)<=30 THEN amount END) d30,
                         SUM(CASE WHEN julianday(?)-julianday(due_at) BETWEEN 31 AND 60 THEN amount END) d60,
                         SUM(CASE WHEN julianday(?)-julianday(due_at)>60 THEN amount END) d60p, SUM(amount) total
                  FROM invoices WHERE paid_at IS NULL""", [as_of] * 5)
    debt = db.q("""SELECT c.name customer, c.region, SUM(v.amount) outstanding, COUNT(*) invoices FROM invoices v JOIN customers c ON c.id=v.customer_id
                   WHERE v.paid_at IS NULL GROUP BY c.id ORDER BY outstanding DESC LIMIT 8""")
    return {
        "kpis": {"items": [kpi("Revenue", rev, "usd", A.pct_change(rev, rev_p), icon="revenue"), kpi("Expenses", exp, "usd", A.pct_change(exp, exp_p), good="down", icon="chart"),
                           kpi("Operating profit", rev - exp, "usd", A.pct_change(rev - exp, rev_p - exp_p), icon="ok"),
                           kpi("Receivables outstanding", ag["total"] or 0, "usd", good="down", icon="warn")], "compare_label": f"vs {prev.label}"},
        "rev_vs_exp": {"series": [{"name": "Revenue", "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in r_s]},
                                  {"name": "Expenses", "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in e_s]}], "fmt": "usd"},
        "expense_mix": {"slices": [{"label": m["key"], "value": m["value"]} for m in mix], "center": {"value": sum(m["value"] for m in mix), "label": "Total", "fmt": "usd_short"}, "fmt": "usd"},
        "aging": {"bars": [{"label": "Not yet due", "value": ag["current_"] or 0}, {"label": "1-30 days", "value": ag["d30"] or 0},
                           {"label": "31-60 days", "value": ag["d60"] or 0}, {"label": "60+ days", "value": ag["d60p"] or 0}], "fmt": "usd"},
        "top_debtors": {"columns": [{"key": "customer", "label": "Customer", "bold": True}, {"key": "region", "label": "Region"},
                                    {"key": "invoices", "label": "Invoices", "fmt": "int"}, {"key": "outstanding", "label": "Outstanding", "fmt": "usd"}], "rows": debt},
    }


# ------------------------------------------------------------------------------------------------------ analytics
ANALYTICS_METRICS = ["revenue", "orders", "units", "avg_order_value", "profit", "expenses", "cancelled_orders", "cancel_rate", "on_time_rate", "po_spend"]


@page("analytics", "Analytics", "chart", "Insights",
      "Self-service analysis workspace: choose a metric (revenue, orders, units, profit, expenses, on-time rate, procurement spend), group it by "
      "month, category, region, product or customer, and compare against the previous period or previous year.",
      "analytics analysis analyze trend compare breakdown explore insights growth period comparison group by",
      [{"key": "metric", "label": "Metric", "type": "select", "options": [opt(m, A.METRICS[m]["label"]) for m in ANALYTICS_METRICS], "default": "revenue",
        "description": "Metric to analyse."},
       {"key": "group_by", "label": "Group by", "type": "select", "default": "month",
        "options": [opt("month", "Month"), opt("category", "Category"), opt("region", "Region"), opt("product", "Product"), opt("customer", "Customer")],
        "description": "Dimension to break the metric down by."},
       _dr(),
       {"key": "compare_to", "label": "Compare to", "type": "select", "default": "none",
        "options": [opt("none", "No comparison"), opt("previous_period", "Previous period"), opt("previous_year", "Same period last year")],
        "description": "Adds a comparison period to the chart and table."},
       {"key": "category", "label": "Category", "type": "select", "options_from": "categories", "description": "Restrict to a product category."},
       {"key": "region", "label": "Region", "type": "select", "options_from": "regions", "description": "Restrict to a region."}],
      [W("summary", "kpi_row", "Summary", "Total for the selected metric and period with change vs the comparison period."),
       W("chart", "line", "Chart", "Metric over time (month) or by dimension (bars), optionally compared to a previous period.", 12),
       W("breakdown", "table", "Breakdown table", "The values behind the chart, with comparison and change.", 12)])
def build_analytics(f, user):
    metric = f.get("metric") if f.get("metric") in ANALYTICS_METRICS else "revenue"
    group = f.get("group_by") or "month"
    if group not in A.METRICS[metric]["dims"]:
        group = "month"
    p = P.resolve(f.get("date_range"), db.as_of())
    cmp_mode = f.get("compare_to") or "none"
    fl = {k: f[k] for k in ("category", "region") if f.get(k)}
    info = A.METRICS[metric]
    cur = A.series(metric, p.start, p.end, group, fl)
    total = A.value(metric, p.start, p.end, fl)
    q = None
    if cmp_mode == "previous_period":
        q = P.previous(p, db.as_of())
    elif cmp_mode == "previous_year":
        q = P.same_period_last_year(p, db.as_of())
    prev = A.series(metric, q.start, q.end, group, fl) if q else None
    prev_total = A.value(metric, q.start, q.end, fl) if q else None
    out = {"summary": {"items": [kpi(info["label"], total, info["fmt"], A.pct_change(total, prev_total) if q else None, good=info["good"], icon="chart",
                                     sub=p.label)], "compare_label": f"vs {q.label}" if q else ""}}
    if group == "month":
        series = [{"name": p.label if q else info["label"], "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in cur]}]
        if prev:
            series.append({"name": q.label, "points": [{"x": s["label"] + (" (MTD)" if s.get("partial") else ""), "y": s["value"]} for s in prev], "dashed": True})
        out["chart"] = {"series": series, "fmt": info["fmt"]}
    else:
        pm = {s["key"]: s["value"] for s in prev} if prev else {}
        out["chart"] = {"bars": [{"label": s["label"], "value": s["value"], "compare": pm.get(s["key"])} for s in cur[:12]], "fmt": info["fmt"], "horizontal": True,
                        "compare_label": q.label if q else None}
    pm = {s["key"]: s["value"] for s in prev} if prev else {}
    rows = [{"name": s["label"], "value": s["value"], "previous": pm.get(s["key"]) if q else None,
             "change": A.pct_change(s["value"], pm.get(s["key"], 0)) if q else None} for s in cur]
    cols = [{"key": "name", "label": "Month" if group == "month" else group.replace("_", " ").title(), "bold": True}, {"key": "value", "label": info["label"], "fmt": info["fmt"]}]
    if q:
        cols += [{"key": "previous", "label": q.label, "fmt": info["fmt"]}, {"key": "change", "label": "Change", "fmt": "pctchg"}]
    out["breakdown"] = {"columns": cols, "rows": rows[:60], "total": len(rows)}
    return out


# ------------------------------------------------------------------------------------------------------- approvals
@page("approvals", "Approvals", "check", "Governance",
      "Human-in-the-loop approval queue: purchase orders above $50,000 and POs submitted for approval wait here for a manager's decision. "
      "Approval limits depend on role.",
      "approvals approve reject pending review sign off authorisation governance queue",
      [{"key": "status", "label": "Status", "type": "select", "default": "pending", "options": [opt("pending", "Pending"), opt("approved", "Approved"), opt("rejected", "Rejected")],
        "description": "Approval status."}],
      [W("kpis", "kpi_row", "Approval summary", "Pending approvals, value waiting, and your approval limit."),
       W("queue", "table", "Approval queue", "Items waiting for a decision with requester, amount and reason; approve or reject in one click.", 12)])
def build_approvals(f, user):
    st = f.get("status") or "pending"
    rows = db.q("SELECT id, type, ref_id, ref_label, amount, requested_by, status, reason, created_at, decided_by FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT 100", (st,))
    pend = db.q1("SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM approvals WHERE status='pending'")
    return {
        "kpis": {"items": [kpi("Pending approvals", pend["n"], "int", good="down", icon="warn"), kpi("Value waiting", pend["v"], "usd", icon="revenue"),
                           kpi("Your approval limit", user["approval_limit"], "usd", icon="ok", sub=user["role"])]},
        "queue": {"columns": [{"key": "ref_label", "label": "Item", "bold": True}, {"key": "type", "label": "Type"}, {"key": "amount", "label": "Amount", "fmt": "usd"},
                              {"key": "requested_by", "label": "Requested by"}, {"key": "reason", "label": "Reason"},
                              {"key": "created_at", "label": "Date", "fmt": "date"}, {"key": "status", "label": "Status", "badge": "status"}],
                  "rows": rows, "total": len(rows), "id_key": "ref_id",
                  "row_actions": [{"id": "po_approve", "label": "Approve", "when": {"status": "pending"}}, {"id": "po_reject", "label": "Reject", "when": {"status": "pending"}, "danger": True}]},
    }


# --------------------------------------------------------------------------------------------------- agent activity
@page("agent_activity", "Agent Activity", "bot", "Governance",
      "Audit trail of everything the AI assistant did on your behalf: requests, the tier that handled them (fast path vs LLM), tools called, "
      "UI actions and latency; plus the benchmark scorecard (intent accuracy, UI-state correctness, retrieval MRR, latency).",
      "agent activity audit log history assistant actions evaluation benchmark accuracy latency kpi scorecard tests",
      [{"key": "tier", "label": "Tier", "type": "select", "options": [opt("fast", "Fast path"), opt("llm", "LLM"), opt("fallback", "Clarification")],
        "description": "How the request was routed."}],
      [W("kpis", "kpi_row", "Assistant performance", "Requests handled, median and p95 latency, share handled by the fast path."),
       W("scorecard", "table", "Benchmark scorecard", "Offline evaluation of the assistant on a labelled utterance corpus.", 12),
       W("log", "table", "Activity log", "Latest assistant requests with intent, tier, latency and outcome.", 12)])
def build_agent_activity(f, user):
    import json
    import statistics
    from .agent import last_eval
    w, prm = ["1=1"], []
    if f.get("tier"):
        w.append("tier=?"); prm.append(f["tier"])
    rows = db.q(f"SELECT ts, user_email, utterance, tier, intent, confidence, tools, status, route_ms, total_ms FROM agent_audit WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT 100", prm)
    allr = db.q("SELECT total_ms, tier FROM agent_audit")
    lat = sorted(r["total_ms"] for r in allr) or [0]
    p50 = statistics.median(lat)
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    fast = sum(r["tier"] == "fast" for r in allr)
    ev = last_eval()
    sc = [{"metric": m["metric"], "value": m["value"], "target": m.get("target", ""), "note": m.get("note", "")} for m in ev.get("metrics", [])] if ev else []
    return {
        "kpis": {"items": [kpi("Requests handled", len(allr), "int", icon="bot"), kpi("Median latency", p50, "ms", good="down", icon="clock"),
                           kpi("p95 latency", p95, "ms", good="down", icon="clock"), kpi("Fast-path share", 100.0 * fast / len(allr) if allr else 0, "pct", icon="ok")]},
        "scorecard": {"columns": [{"key": "metric", "label": "KPI", "bold": True}, {"key": "value", "label": "Measured"}, {"key": "target", "label": "Target"}, {"key": "note", "label": "Notes"}],
                      "rows": sc, "empty": "Run `python -m eval.run_eval` to generate the benchmark scorecard."},
        "log": {"columns": [{"key": "ts", "label": "Time", "fmt": "datetime"}, {"key": "utterance", "label": "Request", "bold": True}, {"key": "intent", "label": "Intent"},
                            {"key": "tier", "label": "Tier", "badge": "status"}, {"key": "total_ms", "label": "Latency", "fmt": "ms"}, {"key": "status", "label": "Outcome", "badge": "status"}],
                "rows": rows, "total": len(rows)},
    }


# ----------------------------------------------------------------------------------------------------- forms
FORMS = {
    "po": {"id": "po", "title": "New purchase order", "op": "po.create", "submit": "Create purchase order", "page": "procurement",
           "description": "Create a purchase order for one product from a supplier. Unit price and expected date fill in from the supplier's price list.",
           "fields": [{"key": "supplier", "label": "Supplier", "type": "select", "options_from": "suppliers", "required": True},
                      {"key": "product", "label": "Product", "type": "select", "options_from": "products", "required": True},
                      {"key": "qty", "label": "Quantity", "type": "number", "required": True},
                      {"key": "unit_price", "label": "Unit price (USD)", "type": "number", "required": True},
                      {"key": "expected_at", "label": "Expected delivery", "type": "date"},
                      {"key": "notes", "label": "Notes", "type": "text"}]},
    "supplier": {"id": "supplier", "title": "Add supplier", "op": "supplier.create", "submit": "Add supplier", "page": "suppliers",
                 "description": "Register a new supplier.",
                 "fields": [{"key": "name", "label": "Supplier name", "type": "text", "required": True},
                            {"key": "country", "label": "Country", "type": "text", "required": True},
                            {"key": "category", "label": "Category", "type": "select", "options_from": "categories", "required": True},
                            {"key": "contact_email", "label": "Contact email", "type": "text"},
                            {"key": "payment_terms", "label": "Payment terms", "type": "select", "options": [opt("Net 30"), opt("Net 45"), opt("Net 60")]},
                            {"key": "lead_time_days", "label": "Lead time (days)", "type": "number"}]},
    "stock": {"id": "stock", "title": "Edit stock", "op": "inventory.update", "submit": "Save changes", "page": "inventory",
              "description": "Adjust stock on hand at a warehouse and/or the reorder level.",
              "fields": [{"key": "product", "label": "Product", "type": "select", "options_from": "products", "required": True},
                         {"key": "warehouse", "label": "Warehouse", "type": "select", "options_from": "warehouses", "required": True},
                         {"key": "set_qty", "label": "Set stock to", "type": "number"}, {"key": "reorder_level", "label": "Reorder level", "type": "number"}]},
    "order_status": {"id": "order_status", "title": "Update order status", "op": "order.update_status", "submit": "Update status", "page": "sales",
                     "description": "Move a sales order to a new status.",
                     "fields": [{"key": "order", "label": "Order", "type": "text", "required": True}, {"key": "status", "label": "New status", "type": "select", "options": SO_STATUS, "required": True}]},
}


def products_options():
    return [opt(r["name"], f"{r['name']} ({r['sku']})") for r in db.q("SELECT name,sku FROM products ORDER BY name")]


def page_meta():
    return [{k: v for k, v in pg.items() if k != "build"} for pg in PAGES.values()]


def build_page(page_id: str, filters: dict, user: dict) -> dict:
    pg = PAGES[page_id]
    data = pg["build"](filters, user)
    widgets = [dict(w, data=data.get(w["id"])) for w in pg["widgets"]]
    return {"page": page_id, "title": pg["title"], "filters": filters, "widgets": widgets}


# Suggested prompts per page ("Suggested insights" in the assistant panel) — also used as demo scripts.
SUGGESTIONS = {
    "dashboard": ["Show revenue trend for the last 12 months", "Compare revenue this month vs last month", "Top 5 products by revenue",
                  "Which products are likely to run out in 14 days?", "Why did revenue drop in March 2026?", "Generate a business report for last month"],
    "inventory": ["Show low stock items sorted by days left", "Which products will run out in the next 14 days?", "Create purchase order drafts for items running out in 14 days",
                  "Compare top 3 suppliers for Steel Rod with best pricing", "Set the reorder level of Copper Wire to 1500"],
    "suppliers": ["Compare top 3 suppliers for Steel Rod with best pricing", "Show suppliers in India rated above 4", "Top 5 suppliers by spend",
                  "Tell me about Global Metals Ltd.", "Which suppliers delivered late in March 2026?"],
    "procurement": ["Show draft purchase orders", "Submit all draft purchase orders", "Create a purchase order for 500 Steel Rod from Global Metals",
                    "Show pending approvals", "How much did we spend on procurement last quarter?"],
    "sales": ["Show cancelled orders for last quarter", "Show European sales for the last 6 months", "Top 5 customers by revenue",
              "Compare revenue Q2 vs Q3", "Why did cancellations increase in March 2026?"],
    "logistics": ["Show delayed shipments", "What is our on-time delivery rate last quarter?", "Compare on-time delivery this quarter vs last quarter",
                  "Which carrier has the worst on-time rate?"],
    "finance": ["Compare profit this quarter vs last quarter", "How much do customers owe us?", "Show expense trend for the last 12 months",
                "Why did profit change in March 2026?"],
    "analytics": ["Analyze sales trend for 2025", "Revenue by category for last quarter", "Compare orders 2025 vs 2026", "Top 5 regions by revenue"],
    "approvals": ["Approve all pending purchase orders under $30k", "Show approved POs", "What is waiting for my approval?"],
    "agent_activity": ["What can you do?", "Open the dashboard", "Show low stock items"],
}
