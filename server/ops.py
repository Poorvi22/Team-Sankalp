"""Operation catalog: every read / write the application exposes.

One declaration per operation drives three things: (1) the REST route the UI calls, (2) the tool the agent may call,
(3) the retrieval document the agent searches to "find the right API". Risk levels gate agent behaviour:
read -> runs immediately, write -> needs human confirmation, destructive -> confirmation with a warning (and drafts only).
"""
import re
from datetime import date, timedelta

from . import analytics as A, db, pages, periods as P

OPS: dict = {}
PO_APPROVAL_THRESHOLD = 50_000.0


class OpError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def op(op_id, method, path, desc, risk="read", params=None, examples=()):
    def deco(fn):
        OPS[op_id] = dict(id=op_id, method=method, path=path, description=desc, risk=risk, params=params or {}, examples=list(examples), fn=fn)
        return fn
    return deco


OP_EXAMPLES = {'meta.get': ['application metadata', 'list pages and filters'], 'page.data': ['load a page', 'what a page shows', 'widget data for a page with filters'], 'metrics.series': ['metric time series', 'revenue by month', 'orders profit expenses trend data', 'breakdown by category or region'], 'metrics.compare': ['compare two periods', 'why did a metric change', 'period over period drivers'], 'inventory.forecast': ['which products will run out', 'stock-out risk', 'days of cover forecast'], 'suppliers.compare': ['compare supplier prices', 'cheapest supplier for a product', 'rank vendors by lead time or reliability'], 'lookup.price': ['supplier price list lookup', 'unit price of a product from a supplier', 'minimum order quantity and lead time'], 'lookup.suppliers_for_product': ['who sells this product', 'suppliers that offer a product'], 'po.create': ['create a purchase order', 'raise a PO', 'order materials from a supplier', 'new draft PO'], 'po.submit': ['submit a draft PO', 'send drafts for approval', 'release purchase orders'], 'po.approve': ['approve a purchase order', 'sign off pending POs', 'clear the approval queue'], 'po.reject': ['reject a purchase order', 'decline a PO'], 'po.receive': ['receive goods', 'goods received note', 'book in a delivery from a supplier', 'mark PO as received into a warehouse'], 'po.delete': ['delete a draft PO', 'discard a draft purchase order'], 'inventory.update': ['adjust stock', 'stock adjustment', 'correct inventory quantity', 'change reorder level or reorder point', 'set stock on hand'], 'order.update_status': ['mark an order as shipped', 'cancel a sales order', 'move order to delivered or processing', 'update order status'], 'supplier.create': ['onboard a new supplier', 'register a vendor', 'add a supplier'], 'supplier.update': ['put a supplier on hold', 'deactivate or suspend a supplier', 'reactivate a vendor']}


def catalog():
    return [{k: v for k, v in o.items() if k != "fn"} for o in OPS.values()]


# ---------------------------------------------------------------------------------------------------- name resolution
def resolve_supplier(x):
    if x in (None, ""):
        return None
    row = db.q1("SELECT * FROM suppliers WHERE id=?", (x,)) if str(x).isdigit() else None
    row = row or db.q1("SELECT * FROM suppliers WHERE lower(name)=lower(?)", (str(x),))
    if not row:
        raise OpError(f"Unknown supplier '{x}'.", 404)
    return row


def resolve_product(x):
    if x in (None, ""):
        return None
    row = db.q1("SELECT * FROM products WHERE id=?", (x,)) if str(x).isdigit() else None
    row = row or db.q1("SELECT * FROM products WHERE lower(sku)=lower(?) OR lower(name)=lower(?)", (str(x), str(x)))
    if not row:
        raise OpError(f"Unknown product '{x}'.", 404)
    return row


def resolve_warehouse(x):
    if x in (None, ""):
        return None
    row = db.q1("SELECT * FROM warehouses WHERE id=?", (x,)) if str(x).isdigit() else None
    row = row or db.q1("SELECT * FROM warehouses WHERE lower(name)=lower(?) OR lower(city)=lower(?)", (str(x), str(x)))
    if not row:
        raise OpError(f"Unknown warehouse '{x}'.", 404)
    return row


# ------------------------------------------------------------------------------------------------- filter validation
def validate_filters(page_id: str, filters: dict):
    """Keep only filters the page declares, with values the control would accept. Returns (clean, rejected)."""
    pg = pages.PAGES[page_id]
    opts = pages.options()
    opts["products"] = pages.products_options()
    clean, rejected = {}, {}
    for fdef in pg["filters"]:
        k = fdef["key"]
        if k not in filters or filters[k] in (None, ""):
            continue
        v = str(filters[k])
        if fdef["type"] == "select":
            allowed = {o["value"] for o in (fdef.get("options") or opts.get(fdef.get("options_from"), []))}
            if v in allowed:
                clean[k] = v
            else:
                rejected[k] = v
        elif fdef["type"] == "search":
            clean[k] = v[:80]
        elif fdef["type"] == "date_range":
            if pages.P.preset(v, db.as_of()) or re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}", v):
                clean[k] = v
            else:
                rejected[k] = v
    for k in filters:
        if k not in {f["key"] for f in pg["filters"]} and filters[k] not in (None, ""):
            rejected[k] = filters[k]
    return clean, rejected


# ------------------------------------------------------------------------------------------------------------- reads
@op("meta.get", "GET", "/api/meta", "Application metadata: pages, widgets, filters, forms and option lists for the current tenant.")
def meta_get(user, p):
    opts = pages.options()
    opts["products"] = pages.products_options()
    return {"pages": pages.page_meta(), "forms": pages.FORMS, "options": opts, "as_of": db.as_of().isoformat(),
            "data_start": db.meta("data_start"), "suggestions": pages.SUGGESTIONS, "ops": {o["id"]: {"method": o["method"], "path": o["path"], "risk": o["risk"]} for o in OPS.values()},
            "pending_approvals": db.scalar("SELECT COUNT(*) FROM approvals WHERE status='pending'"), "presets": [{"value": a, "label": b} for a, b in P.PRESETS],
            "user": {k: user[k] for k in ("email", "name", "role", "approval_limit")}}


@op("page.data", "GET", "/api/page", "Load all widget data for a page with the given filters (what the page currently shows).",
    params={"page": "page id"})
def page_data(user, p):
    pid = p.get("page")
    if pid not in pages.PAGES:
        raise OpError(f"Unknown page '{pid}'.", 404)
    clean, _ = validate_filters(pid, {k: v for k, v in p.items() if k != "page"})
    return pages.build_page(pid, clean, user)


@op("metrics.series", "GET", "/api/metrics/series",
    "Time series or breakdown of a business metric (revenue, orders, units, profit, expenses, on-time rate...) over a date range, grouped by month, category, region, product or customer.",
    params={"metric": "metric id", "start": "ISO date", "end": "ISO date", "group_by": "dimension"})
def metrics_series(user, p):
    m = p.get("metric", "revenue")
    if m not in A.METRICS:
        raise OpError(f"Unknown metric '{m}'.", 404)
    start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
    return {"metric": m, "series": A.series(m, start, end, p.get("group_by", "month"), p.get("filters") or {})}


@op("metrics.compare", "GET", "/api/metrics/compare",
    "Compare a metric between two periods and explain the change with drivers by category, region and product.",
    params={"metric": "metric id", "a_start": "", "a_end": "", "b_start": "", "b_end": ""})
def metrics_compare(user, p):
    pa = P.resolve(f"{p['a_start']}..{p['a_end']}", db.as_of())
    pb = P.resolve(f"{p['b_start']}..{p['b_end']}", db.as_of())
    return A.diagnose(p["metric"], pa, pb, p.get("filters") or {})


@op("inventory.forecast", "GET", "/api/inventory/forecast",
    "Products likely to run out of stock within N days, based on current stock and the last 60 days of demand.", params={"horizon": "days"})
def inventory_forecast(user, p):
    return {"rows": A.stockout_forecast(int(p.get("horizon", 14)), int(p["warehouse"]) if p.get("warehouse") else None)}


@op("suppliers.compare", "GET", "/api/suppliers/compare",
    "Rank suppliers for one or more products by price, lead time, on-time delivery and rating.", params={"products": "list", "criterion": "price|lead_time|reliability|balanced"})
def suppliers_compare(user, p):
    return {"rows": A.rank_suppliers(p["products"], p.get("criterion", "balanced"))}


@op("lookup.price", "GET", "/api/lookup/price", "Supplier's unit price, minimum order quantity and lead time for a product (used to auto-fill purchase orders).",
    params={"supplier": "", "product": ""})
def lookup_price(user, p):
    s, pr = resolve_supplier(p.get("supplier")), resolve_product(p.get("product"))
    row = db.q1("SELECT * FROM supplier_prices WHERE supplier_id=? AND product_id=?", (s["id"], pr["id"]))
    if not row:
        offers = db.q("SELECT s.name FROM supplier_prices sp JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.product_id=?", (pr["id"],))
        return {"found": False, "suppliers": [o["name"] for o in offers]}
    exp = (db.as_of() + timedelta(days=row["lead_time_days"])).isoformat()
    return {"found": True, "unit_price": row["unit_price"], "min_qty": row["min_qty"], "lead_time_days": row["lead_time_days"], "expected_at": exp, "unit": pr["unit"]}


@op("lookup.suppliers_for_product", "GET", "/api/lookup/suppliers", "Suppliers that sell a given product.", params={"product": ""})
def lookup_suppliers(user, p):
    pr = resolve_product(p.get("product"))
    return {"suppliers": [r["name"] for r in db.q("SELECT s.name FROM supplier_prices sp JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.product_id=? AND s.status='active' ORDER BY sp.unit_price", (pr["id"],))]}


# ------------------------------------------------------------------------------------------------------------ writes
def _next_po_number(year):
    n = db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE po_number LIKE ?", (f"PO-{year}-%",)) + 1
    return f"PO-{year}-{n:04d}"


@op("po.create", "POST", "/api/procurement/orders",
    "Create a purchase order (saved as draft) for one or more products from a supplier. Unit price defaults to the supplier's price list.",
    risk="write", params={"supplier": "name or id", "items": "[{product, qty, unit_price?}]", "expected_at": "", "notes": ""})
def po_create(user, p):
    s = resolve_supplier(p.get("supplier"))
    if not s:
        raise OpError("A supplier is required.")
    if s["status"] != "active":
        raise OpError(f"{s['name']} is {s['status'].replace('_', ' ')} and cannot receive new purchase orders.")
    items = p.get("items") or ([{"product": p.get("product"), "qty": p.get("qty"), "unit_price": p.get("unit_price")}] if p.get("product") else [])
    if not items:
        raise OpError("At least one product line is required.")
    lines, total, lead, warnings = [], 0.0, 0, []
    for it in items:
        pr = resolve_product(it.get("product"))
        sp = db.q1("SELECT * FROM supplier_prices WHERE supplier_id=? AND product_id=?", (s["id"], pr["id"]))
        if not sp:
            raise OpError(f"{s['name']} does not supply {pr['name']}.")
        qty = int(float(it.get("qty") or 0))
        if qty <= 0:
            raise OpError(f"Quantity for {pr['name']} must be positive.")
        if qty < sp["min_qty"]:
            warnings.append(f"{pr['name']}: raised quantity {qty} -> {sp['min_qty']} (supplier minimum order).")
            qty = sp["min_qty"]
        price = float(it.get("unit_price") or sp["unit_price"])
        lines.append((pr["id"], qty, price, pr["name"]))
        total += qty * price
        lead = max(lead, sp["lead_time_days"])
    today = db.as_of()
    exp = p.get("expected_at") or (today + timedelta(days=lead)).isoformat()
    conn = db.get_db()
    cur = conn.execute("INSERT INTO purchase_orders(po_number,supplier_id,status,created_at,expected_at,total,created_by,notes) VALUES(?,?,?,?,?,?,?,?)",
                       (_next_po_number(today.year), s["id"], "draft", today.isoformat(), exp, round(total, 2), user["email"], p.get("notes")))
    po_id = cur.lastrowid
    conn.executemany("INSERT INTO po_items(po_id,product_id,qty,unit_price) VALUES(?,?,?,?)", [(po_id, a, b, c) for a, b, c, _ in lines])
    conn.commit()
    db.bump()
    po = db.q1("SELECT * FROM purchase_orders WHERE id=?", (po_id,))
    return {"po": po, "supplier": s["name"], "lines": [{"product": n, "qty": q, "unit_price": pr_} for _, q, pr_, n in lines], "warnings": warnings,
            "needs_approval": total > PO_APPROVAL_THRESHOLD}


def _po(ref):
    row = db.q1("SELECT * FROM purchase_orders WHERE id=?", (ref,)) if str(ref).isdigit() else None
    row = row or db.q1("SELECT * FROM purchase_orders WHERE upper(po_number)=upper(?)", (str(ref),))
    if not row:
        raise OpError(f"Purchase order '{ref}' not found.", 404)
    return row


@op("po.submit", "POST", "/api/procurement/submit", "Submit a draft purchase order: auto-approved up to $50,000, above that it goes to the approval queue.",
    risk="write", params={"po": "PO number or id"})
def po_submit(user, p):
    po = _po(p.get("po"))
    if po["status"] != "draft":
        raise OpError(f"{po['po_number']} is {po['status'].replace('_', ' ')}; only drafts can be submitted.")
    conn = db.get_db()
    if po["total"] > PO_APPROVAL_THRESHOLD:
        conn.execute("UPDATE purchase_orders SET status='pending_approval' WHERE id=?", (po["id"],))
        conn.execute("INSERT INTO approvals(type,ref_id,ref_label,amount,requested_by,status,reason,created_at) VALUES('purchase_order',?,?,?,?,'pending',?,?)",
                     (po["id"], po["po_number"], po["total"], user["email"], "PO value above $50,000 requires approval", db.as_of().isoformat()))
        new = "pending_approval"
    else:
        conn.execute("UPDATE purchase_orders SET status='approved' WHERE id=?", (po["id"],))
        new = "approved"
    conn.commit()
    db.bump()
    return {"po_number": po["po_number"], "status": new, "total": po["total"]}


@op("po.approve", "POST", "/api/procurement/approve", "Approve a purchase order that is waiting in the approval queue (limited by the approver's role).",
    risk="write", params={"po": "PO number or id"})
def po_approve(user, p):
    po = _po(p.get("po"))
    if po["status"] != "pending_approval":
        raise OpError(f"{po['po_number']} is {po['status'].replace('_', ' ')}, not waiting for approval.")
    if po["total"] > user["approval_limit"]:
        raise OpError(f"{po['po_number']} (${po['total']:,.0f}) exceeds your approval limit of ${user['approval_limit']:,.0f}. An Admin must approve it.", 403)
    conn = db.get_db()
    conn.execute("UPDATE purchase_orders SET status='approved' WHERE id=?", (po["id"],))
    conn.execute("UPDATE approvals SET status='approved', decided_by=?, decided_at=? WHERE type='purchase_order' AND ref_id=? AND status='pending'",
                 (user["email"], db.as_of().isoformat(), po["id"]))
    conn.commit()
    db.bump()
    return {"po_number": po["po_number"], "status": "approved", "total": po["total"]}


@op("po.reject", "POST", "/api/procurement/reject", "Reject a purchase order in the approval queue (cancels it).", risk="write", params={"po": "PO number or id"})
def po_reject(user, p):
    po = _po(p.get("po"))
    if po["status"] != "pending_approval":
        raise OpError(f"{po['po_number']} is {po['status'].replace('_', ' ')}, not waiting for approval.")
    conn = db.get_db()
    conn.execute("UPDATE purchase_orders SET status='cancelled' WHERE id=?", (po["id"],))
    conn.execute("UPDATE approvals SET status='rejected', decided_by=?, decided_at=? WHERE type='purchase_order' AND ref_id=? AND status='pending'",
                 (user["email"], db.as_of().isoformat(), po["id"]))
    conn.commit()
    db.bump()
    return {"po_number": po["po_number"], "status": "cancelled", "total": po["total"]}


@op("po.receive", "POST", "/api/procurement/receive",
    "Mark an approved/ordered purchase order as received and add the quantities to stock in the receiving warehouse.", risk="write",
    params={"po": "PO number or id", "warehouse": "receiving warehouse (default Chicago DC)"})
def po_receive(user, p):
    po = _po(p.get("po"))
    if po["status"] not in ("approved", "ordered"):
        raise OpError(f"{po['po_number']} is {po['status'].replace('_', ' ')}; only approved or ordered POs can be received.")
    wh = resolve_warehouse(p.get("warehouse") or 1)
    conn = db.get_db()
    for it in db.q("SELECT * FROM po_items WHERE po_id=?", (po["id"],)):
        conn.execute("UPDATE inventory SET qty=qty+? WHERE product_id=? AND warehouse_id=?", (it["qty"], it["product_id"], wh["id"]))
    conn.execute("UPDATE purchase_orders SET status='received', received_at=? WHERE id=?", (db.as_of().isoformat(), po["id"]))
    conn.commit()
    db.bump()
    return {"po_number": po["po_number"], "status": "received", "warehouse": wh["name"]}


@op("po.delete", "DELETE", "/api/procurement/orders", "Permanently delete a DRAFT purchase order (other statuses cannot be deleted).",
    risk="destructive", params={"po": "PO number or id"})
def po_delete(user, p):
    po = _po(p.get("po"))
    if po["status"] != "draft":
        raise OpError(f"{po['po_number']} is {po['status'].replace('_', ' ')}. Only draft purchase orders can be deleted.", 409)
    conn = db.get_db()
    conn.execute("DELETE FROM po_items WHERE po_id=?", (po["id"],))
    conn.execute("DELETE FROM purchase_orders WHERE id=?", (po["id"],))
    conn.commit()
    db.bump()
    return {"po_number": po["po_number"], "deleted": True}


@op("inventory.update", "POST", "/api/inventory/update", "Set stock on hand at a warehouse and/or change the reorder level of a product.",
    risk="write", params={"product": "name/sku", "warehouse": "name (default: all warehouses for reorder level)", "set_qty": "int", "reorder_level": "int"})
def inventory_update(user, p):
    pr = resolve_product(p.get("product"))
    wh = resolve_warehouse(p.get("warehouse")) if p.get("warehouse") else None
    set_qty = p.get("set_qty")
    reorder = p.get("reorder_level")
    if set_qty in (None, "") and reorder in (None, ""):
        raise OpError("Nothing to change: give a new stock quantity and/or a reorder level.")
    conn = db.get_db()
    before = db.q("SELECT warehouse_id, qty, reorder_level FROM inventory WHERE product_id=?", (pr["id"],))
    if set_qty not in (None, ""):
        if int(set_qty) < 0:
            raise OpError("Stock cannot be negative.")
        if not wh:
            raise OpError("Which warehouse? Stock is held per warehouse.")
        conn.execute("UPDATE inventory SET qty=? WHERE product_id=? AND warehouse_id=?", (int(set_qty), pr["id"], wh["id"]))
    if reorder not in (None, ""):
        if int(reorder) < 0:
            raise OpError("Reorder level cannot be negative.")
        if wh:
            conn.execute("UPDATE inventory SET reorder_level=? WHERE product_id=? AND warehouse_id=?", (int(reorder), pr["id"], wh["id"]))
        else:   # product-level request: distribute proportionally to each warehouse's current reorder level
            tot = sum(b["reorder_level"] for b in before) or len(before)
            for b in before:
                share = (b["reorder_level"] / tot) if sum(x["reorder_level"] for x in before) else 1 / len(before)
                conn.execute("UPDATE inventory SET reorder_level=? WHERE product_id=? AND warehouse_id=?", (round(int(reorder) * share), pr["id"], b["warehouse_id"]))
    conn.commit()
    db.bump()
    after = db.q1("SELECT SUM(qty) qty, SUM(reorder_level) reorder FROM inventory WHERE product_id=?", (pr["id"],))
    return {"product": pr["name"], "warehouse": wh["name"] if wh else "all", "total_qty": after["qty"], "total_reorder_level": after["reorder"]}


ORDER_FLOW = {"pending": ["processing", "cancelled"], "processing": ["shipped", "cancelled"], "shipped": ["delivered", "cancelled"],
              "delivered": [], "cancelled": []}


@op("order.update_status", "POST", "/api/sales/order-status", "Move a sales order to a new status (pending -> processing -> shipped -> delivered, or cancelled).",
    risk="write", params={"order": "SO number or id", "status": "new status"})
def order_status(user, p):
    ref = p.get("order")
    o = db.q1("SELECT * FROM sales_orders WHERE id=?", (ref,)) if str(ref).isdigit() and len(str(ref)) < 6 else None
    o = o or db.q1("SELECT * FROM sales_orders WHERE upper(so_number)=upper(?)", (str(ref),))
    if not o:
        raise OpError(f"Order '{ref}' not found.", 404)
    new = p.get("status")
    if new not in ORDER_FLOW:
        raise OpError(f"Unknown status '{new}'.")
    if new not in ORDER_FLOW[o["status"]]:
        raise OpError(f"{o['so_number']} is {o['status']}; it can move to: {', '.join(ORDER_FLOW[o['status']]) or 'nothing (final state)'}.", 409)
    conn = db.get_db()
    today = db.as_of().isoformat()
    conn.execute("UPDATE sales_orders SET status=?, delivered_date=? WHERE id=?", (new, today if new == "delivered" else o["delivered_date"], o["id"]))
    if new == "shipped" and not db.q1("SELECT 1 FROM shipments WHERE order_id=?", (o["id"],)):
        wh = db.q1("SELECT city FROM warehouses WHERE id=?", (o["warehouse_id"],))
        conn.execute("INSERT INTO shipments(order_id,carrier,status,shipped_at,eta,delay_days,origin,destination) VALUES(?,?,?,?,?,?,?,?)",
                     (o["id"], "FastFreight", "in_transit", today, o["promised_date"], 0, wh["city"], o["region"]))
    if new == "delivered":
        conn.execute("UPDATE shipments SET status='delivered', delivered_at=?, delay_days=MAX(0, julianday(?)-julianday(eta)) WHERE order_id=?", (today, today, o["id"]))
    conn.commit()
    db.bump()
    return {"so_number": o["so_number"], "from": o["status"], "status": new}


@op("supplier.create", "POST", "/api/suppliers", "Register a new supplier.", risk="write",
    params={"name": "", "country": "", "category": "", "contact_email": "", "payment_terms": "", "lead_time_days": ""})
def supplier_create(user, p):
    name, country, cat = (p.get("name") or "").strip(), (p.get("country") or "").strip(), p.get("category")
    if not name or not country or not cat:
        raise OpError("Name, country and category are required.")
    if db.q1("SELECT 1 FROM suppliers WHERE lower(name)=lower(?)", (name,)):
        raise OpError(f"A supplier called '{name}' already exists.", 409)
    if cat not in {o["value"] for o in pages.options()["categories"]}:
        raise OpError(f"Unknown category '{cat}'.")
    conn = db.get_db()
    cur = conn.execute("INSERT INTO suppliers(name,country,category,rating,on_time_pct,lead_time_days,payment_terms,status,contact_email,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (name, country, cat, 4.0, 90.0, int(p.get("lead_time_days") or 10), p.get("payment_terms") or "Net 30", "active", p.get("contact_email"), db.as_of().isoformat()))
    conn.commit()
    db.bump()
    return {"id": cur.lastrowid, "name": name}


@op("supplier.update", "POST", "/api/suppliers/update", "Change a supplier's status (active / on_hold / inactive).", risk="write",
    params={"supplier": "name or id", "status": "active|on_hold|inactive"})
def supplier_update(user, p):
    s = resolve_supplier(p.get("supplier"))
    if p.get("status") not in ("active", "on_hold", "inactive"):
        raise OpError("Status must be active, on_hold or inactive.")
    conn = db.get_db()
    conn.execute("UPDATE suppliers SET status=? WHERE id=?", (p["status"], s["id"]))
    conn.commit()
    db.bump()
    return {"supplier": s["name"], "from": s["status"], "status": p["status"]}


# ------------------------------------------------------------------------------------------------------- invocation
def call(op_id: str, user: dict, params: dict | None = None):
    o = OPS.get(op_id)
    if not o:
        raise OpError(f"Unknown operation '{op_id}'.", 404)
    return o["fn"](user, params or {})


def register_routes(app, current_user):
    """Expose every operation as a REST endpoint (the UI uses these; the agent calls `call` directly)."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse

    def make(o):
        async def endpoint(request: Request, user=Depends(current_user)):
            params = dict(request.query_params)
            if request.method != "GET":
                try:
                    body = await request.json()
                    if isinstance(body, dict):
                        params.update(body)
                except Exception:
                    pass
            try:
                return o["fn"](user, params)
            except OpError as e:
                return JSONResponse({"error": str(e)}, status_code=e.code)
            except (KeyError, ValueError) as e:
                return JSONResponse({"error": f"Invalid request: {e}"}, status_code=400)
        return endpoint

    for o in OPS.values():
        app.add_api_route(o["path"], make(o), methods=[o["method"]], name=o["id"])
