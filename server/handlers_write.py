"""State-changing intent handlers. None of them writes: each *prepares* a plan, shows exactly what will change, and emits a
confirmation (or pre-fills a form). The write happens only when the user confirms (see agent.confirm)."""
import math
import re
import time
from datetime import timedelta

from . import analytics as A, db, ops, pages
from .agentkit import card, chips, msg, nav, new_confirmation, plural, src, step, usd
from .handlers_read import HANDLERS, handler


def _ceil10(x):
    return int(math.ceil(max(x, 1) / 10.0) * 10)


def _pick_product(run, verb="use"):
    """-> (product row | None, events-to-yield-if-ambiguous)."""
    p = run.p
    if p.ents.get("product"):
        return db.q1("SELECT * FROM products WHERE id=?", (p.ents["product"][0].id,)), []
    if p.product_group:
        ids = p.product_group[1]
        rows = db.q(f"SELECT * FROM products WHERE id IN ({','.join('?' * len(ids))}) ORDER BY name", ids)
        if len(rows) == 1:
            return rows[0], []
        return None, [msg(f"“{p.product_group[0]}” matches {len(rows)} products — which one should I {verb}? " + ", ".join(f"**{r['name']}**" for r in rows)),
                      chips([f"{run.message.rstrip('.?!')} — {r['name']}" for r in rows[:4]])]
    return None, [msg("Which product do you mean? Name it (for example **Steel Rod** or its SKU **SR-001**)."), chips(["Create a purchase order for 500 Steel Rod"])]


def _qty(p):
    for v, *_ in p.numbers:
        if v >= 1:
            return int(v)
    return None


def _po_lines_text(items):
    return [f"{i['product']} × {i['qty']:,} @ ${i['unit_price']:,.2f}" for i in items]


# ----------------------------------------------------------------------------------------------------------- create_po
@handler("create_po")
def h_create_po(run):
    p = run.p
    pr, ask = _pick_product(run, "order")
    if not pr:
        for e in ask:
            yield e
        run.status = "clarify"
        return
    stock = next((r for r in A.stock_rows() if r["pid"] == pr["id"]), None)
    yield step("a", f"Looking up suppliers and prices for {pr['name']}…")
    if p.ents.get("supplier"):
        sid = p.ents["supplier"][0].id
        offer = next((o for o in A.supplier_offers((pr["id"],)) if o["supplier_id"] == sid), None)
        if not offer:
            names = [o["supplier"] for o in sorted(A.supplier_offers((pr["id"],)), key=lambda o: o["unit_price"])]
            yield step("a", "Supplier does not offer this product", "error")
            yield msg(f"**{p.ents['supplier'][0].name}** doesn't supply **{pr['name']}**. Suppliers that do: " + ", ".join(f"**{n}**" for n in names[:5]) + ".")
            yield chips([f"Create a purchase order for {pr['name']} from {n}" for n in names[:3]])
            run.status = "clarify"
            return
        chosen_by = "as requested"
    else:
        ranked = A.rank_suppliers([pr["id"]], "price")
        if not ranked:
            yield msg(f"No active supplier offers **{pr['name']}**.")
            run.status = "error"
            return
        sid = ranked[0]["supplier_id"]
        offer = ranked[0]["items"][0]
        chosen_by = "best price"
    qty = _qty(p)
    suggested = False
    if not qty:
        need = (stock["daily_demand"] * 45 - stock["qty"]) if stock else 0
        qty = _ceil10(max(need, offer["min_qty"]))
        suggested = True
    qty = max(qty, offer["min_qty"])
    expected = (run.as_of + timedelta(days=offer["lead_time_days"])).isoformat()
    values = {"supplier": offer["supplier"], "product": pr["name"], "qty": qty, "unit_price": offer["unit_price"], "expected_at": expected, "notes": "Created by BizPilot assistant"}
    total = qty * offer["unit_price"]
    yield step("a", "Purchase order prepared", "done")
    yield nav("procurement", {}, label="New purchase order")
    yield {"t": "ui", "a": "open_form", "form": "po", "values": values}
    warn = []
    if total > ops.PO_APPROVAL_THRESHOLD:
        warn.append(f"Total {usd(total)} is above $50,000 — after you submit it, it will need approval.")
    if suggested:
        warn.append(f"No quantity given — I suggested {qty:,} (about 45 days of demand, minus current stock).")
    yield msg(f"I opened the **New purchase order** form and filled it in: **{qty:,} × {pr['name']}** from **{offer['supplier']}** ({chosen_by}) at ${offer['unit_price']:,.2f}/{pr['unit']} = **{usd(total)}**, expected {expected}."
              "\nReview it and press **Create purchase order**, or let me save it as a draft with the button below.")
    yield new_confirmation(run.user, f"Create draft PO · {offer['supplier']}", [("po.create", {"supplier": offer["supplier"], "items": [{"product": pr["name"], "qty": qty, "unit_price": offer["unit_price"]}],
                                                                                     "expected_at": expected, "notes": "Created by BizPilot assistant"})],
                           lines=[f"{pr['name']} × {qty:,} @ ${offer['unit_price']:,.2f} = {usd(total)}", f"Supplier: {offer['supplier']} · expected {expected}"], warnings=warn, group="po.create",
                           after=[nav("procurement", {"status": "draft"}, label="draft POs")], button="Create draft PO")


# ------------------------------------------------------------------------------------------------------- create_po_bulk
def plan_replenishment(run, horizon, include_low_status):
    rows = A.stockout_forecast(horizon)
    if include_low_status:
        have = {r["pid"] for r in rows}
        rows += [r for r in A.stock_rows() if r["status"] in ("low_stock", "out_of_stock") and r["pid"] not in have]
    rows.sort(key=lambda r: r["days_left"])
    plan, skipped = [], []
    for r in rows:
        ranked = A.rank_suppliers([r["pid"]], "balanced")
        if not ranked:
            skipped.append(r["name"])
            continue
        best = ranked[0]
        it = best["items"][0]
        need = r["daily_demand"] * 45 - r["qty"] if r["daily_demand"] else r["reorder_level"] * 2 - r["qty"]
        qty = max(_ceil10(need), it["min_qty"])
        plan.append({"pid": r["pid"], "product": r["name"], "qty": qty, "unit_price": it["unit_price"], "lead": it["lead_time_days"], "supplier": best["supplier"],
                     "supplier_id": best["supplier_id"], "days_left": r["days_left"], "stock": r["qty"], "alt": ranked[1]["supplier"] if len(ranked) > 1 else None})
    return plan, skipped


@handler("create_po_bulk")
def h_create_po_bulk(run):
    p = run.p
    horizon = p.horizon_days or 14
    include_low = p.rx(r"low[- ]stock|below (?:the )?reorder|reorder level|out[- ]of[- ]stock|shortage|running low|replenish|restock")
    yield step("1", f"Analyzing inventory — items that may run out within {horizon} days" + (" or are below reorder level" if include_low else "") + "…")
    t = time.perf_counter()
    plan, skipped = run.timed("plan_replenishment", plan_replenishment, run, horizon, include_low)
    yield step("1", f"{plural(len(plan), 'product')} need replenishing", "done", round((time.perf_counter() - t) * 1000))
    if not plan:
        yield msg(f"Nothing needs replenishing: no product is projected to run out within **{horizon} days**" + (" and none is below its reorder level" if include_low else "") + ".")
        return
    yield step("2", "Comparing suppliers (price, lead time, on-time, rating)…")
    yield step("2", "Best supplier chosen per product", "done", 0)
    yield step("3", "Sizing orders to cover ~45 days of demand and grouping by supplier…")
    by_sup = {}
    for it in plan:
        by_sup.setdefault(it["supplier"], []).append(it)
    calls, lines, warn, total_all = [], [], [], 0.0
    for sup, items in by_sup.items():
        tot = sum(i["qty"] * i["unit_price"] for i in items)
        total_all += tot
        calls.append(("po.create", {"supplier": sup, "items": [{"product": i["product"], "qty": i["qty"], "unit_price": i["unit_price"]} for i in items],
                                     "notes": f"Auto-replenishment ({horizon}-day cover) by BizPilot assistant"}))
        lines.append(f"**{sup}** — {usd(tot)}" + (" ⚠ needs approval" if tot > ops.PO_APPROVAL_THRESHOLD else "") + ": " + "; ".join(f"{i['product']} × {i['qty']:,}" for i in items))
        if tot > ops.PO_APPROVAL_THRESHOLD:
            warn.append(f"The PO for {sup} ({usd(tot)}) exceeds $50,000 and will go to the approval queue when submitted.")
    yield step("3", f"{plural(len(calls), 'draft purchase order')} prepared", "done", 0)
    yield step("4", "Ready for your review", "done", 0)
    yield card("table", title=f"Replenishment plan — {plural(len(plan), 'product')}, {usd(total_all)}",
               columns=[{"key": "product", "label": "Product", "bold": True}, {"key": "stock", "label": "Stock", "fmt": "int"}, {"key": "days_left", "label": "Days left", "fmt": "days", "badge": "days"},
                        {"key": "qty", "label": "Order qty", "fmt": "int"}, {"key": "supplier", "label": "Best supplier"}, {"key": "unit_price", "label": "Unit price", "fmt": "usd2"}], rows=plan,
               sources=[src("Open Inventory", "inventory", {"sort": "days_left:asc"})])
    yield nav("inventory", {"sort": "days_left:asc"}, label="sorted by days left")
    yield msg(f"I found **{plural(len(plan), 'product')}** at risk and prepared **{plural(len(calls), 'draft purchase order')}** (total **{usd(total_all)}**), picking the best-scoring supplier for each product. Nothing is saved yet — please review."
              + (f"\nNo supplier available for: {', '.join(skipped)}." if skipped else ""))
    yield new_confirmation(run.user, f"Create {plural(len(calls), 'draft PO')} · {usd(total_all)}", calls, lines=lines, warnings=warn, group="po.bulk",
                           after=[nav("procurement", {"status": "draft"}, label="draft POs")], button=f"Create {len(calls)} drafts")


# ----------------------------------------------------------------------------------------------------------- PO workflow
def _pos_by_status(status):
    return db.q("SELECT po.*, s.name supplier FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE po.status=? ORDER BY po.created_at DESC, po.id DESC", (status,))


def _po_row(ref):
    return db.q1("SELECT po.*, s.name supplier FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE upper(po.po_number)=upper(?)", (ref,))


@handler("submit_po")
def h_submit(run):
    p = run.p
    if p.po_refs:
        rows = [r for r in (_po_row(x) for x in p.po_refs) if r]
    else:
        rows = _pos_by_status("draft")
    bad = [r for r in rows if r["status"] != "draft"]
    rows = [r for r in rows if r["status"] == "draft"]
    if not rows:
        yield msg("There are no draft purchase orders to submit." if not p.po_refs else "That PO isn't a draft, so it can't be submitted.")
        run.status = "clarify"
        return
    lines = [f"{r['po_number']} · {r['supplier']} · {usd(r['total'])} → " + ("approval queue" if r["total"] > ops.PO_APPROVAL_THRESHOLD else "approved") for r in rows]
    yield nav("procurement", {"status": "draft"}, label="draft POs")
    yield msg(f"Ready to submit **{plural(len(rows), 'draft PO')}**. POs up to $50,000 are approved automatically; larger ones go to the approval queue.")
    yield new_confirmation(run.user, f"Submit {plural(len(rows), 'draft PO')}", [("po.submit", {"po": r["po_number"]}) for r in rows], lines=lines, group="po.submit",
                           after=[nav("procurement", {}, label="all POs")], button="Submit")


@handler("approve_po")
def h_approve(run):
    p = run.p
    reject = p.rx(r"\b(reject|decline|deny)\b")
    if p.po_refs:
        rows = [r for r in (_po_row(x) for x in p.po_refs) if r and r["status"] == "pending_approval"]
    else:
        rows = _pos_by_status("pending_approval")
    if p.money:
        rows = [r for r in rows if (r["total"] <= p.money[1] if p.money[0] == "lt" else r["total"] >= p.money[1])]
    if not rows:
        yield msg("No purchase orders are waiting for approval" + (" that match your condition." if p.money or p.po_refs else "."))
        run.status = "clarify"
        return
    limit = run.user["approval_limit"]
    ok = rows if reject else [r for r in rows if r["total"] <= limit]
    over = [] if reject else [r for r in rows if r["total"] > limit]
    verb = "reject" if reject else "approve"
    yield nav("approvals", {"status": "pending"}, label="pending")
    if not ok:
        yield msg(f"All {len(rows)} matching POs exceed your approval limit of **{usd(limit)}** — an Admin has to approve them: " + ", ".join(r["po_number"] for r in over) + ".")
        run.status = "refused"
        return
    warn = [f"{len(over)} PO(s) above your {usd(limit)} limit are left out: " + ", ".join(f"{r['po_number']} ({usd(r['total'])})" for r in over)] if over else []
    yield msg(f"Ready to **{verb} {plural(len(ok), 'purchase order')}** worth **{usd(sum(r['total'] for r in ok))}**." + (" Some are above your approval limit and are excluded." if over else ""))
    yield new_confirmation(run.user, f"{verb.title()} {plural(len(ok), 'purchase order')}", [(f"po.{verb}", {"po": r["po_number"]}) for r in ok], risk="write",
                           lines=[f"{r['po_number']} · {r['supplier']} · {usd(r['total'])}" for r in ok], warnings=warn, group="po.approve", after=[nav("approvals", {"status": "pending"}, label="pending")], button=verb.title())


@handler("receive_po")
def h_receive(run):
    p = run.p
    if not p.po_refs:
        yield msg("Which purchase order was received? Give me the PO number, e.g. **PO-2026-0399**.")
        run.status = "clarify"
        return
    po = _po_row(p.po_refs[0])
    if not po:
        yield msg(f"I can't find **{p.po_refs[0]}**.")
        run.status = "error"
        return
    if po["status"] not in ("approved", "ordered"):
        yield msg(f"**{po['po_number']}** is **{po['status'].replace('_', ' ')}**; only approved or ordered POs can be received.")
        run.status = "refused"
        return
    wh = db.q1("SELECT * FROM warehouses WHERE id=?", (p.ents["warehouse"][0].id if p.ents.get("warehouse") else 1,))
    items = db.q("SELECT pr.id pid, pr.name, i.qty FROM po_items i JOIN products pr ON pr.id=i.product_id WHERE i.po_id=?", (po["id"],))
    changes = []
    for it in items:
        cur = db.scalar("SELECT qty FROM inventory WHERE product_id=? AND warehouse_id=?", (it["pid"], wh["id"]))
        changes.append({"label": f"{it['name']} @ {wh['name']}", "before": f"{cur:,}", "after": f"{cur + it['qty']:,}"})
    yield nav("procurement", {"search": po["po_number"]}, label=po["po_number"])
    yield msg(f"Receiving **{po['po_number']}** ({po['supplier']}, {usd(po['total'])}) into **{wh['name']}** will add stock as shown.")
    yield new_confirmation(run.user, f"Receive {po['po_number']} into {wh['name']}", [("po.receive", {"po": po["po_number"], "warehouse": wh["name"]})], changes=changes, group="po.receive",
                           after=[nav("inventory", {"warehouse": str(wh["id"])}, label=wh["name"])], button="Receive")


@handler("delete_po")
def h_delete(run):
    p = run.p
    if p.po_refs:
        found = [r for r in (_po_row(x) for x in p.po_refs) if r]
    elif p.rx(r"\ball\b|every|drafts|get rid"):
        found = _pos_by_status("draft")
    else:
        yield msg("Which purchase order should I delete? Give me a PO number — only **draft** POs can be deleted.")
        run.status = "clarify"
        return
    drafts = [r for r in found if r["status"] == "draft"]
    refused = [r for r in found if r["status"] != "draft"]
    if not drafts:
        yield msg("I can only delete **draft** purchase orders" + (": " + ", ".join(f"{r['po_number']} is {r['status'].replace('_', ' ')}" for r in refused) if refused else " — there are none.") + ".")
        run.status = "refused"
        return
    yield nav("procurement", {"status": "draft"}, label="draft POs")
    yield msg(f"This will **permanently delete {plural(len(drafts), 'draft PO')}**. It can't be undone.")
    yield new_confirmation(run.user, f"Delete {plural(len(drafts), 'draft PO')}", [("po.delete", {"po": r["po_number"]}) for r in drafts], risk="destructive",
                           lines=[f"{r['po_number']} · {r['supplier']} · {usd(r['total'])}" for r in drafts],
                           warnings=[f"Not deleted (not drafts): " + ", ".join(f"{r['po_number']} ({r['status'].replace('_', ' ')})" for r in refused)] if refused else [], group="po.delete",
                           after=[nav("procurement", {"status": "draft"}, label="draft POs")], button="Delete permanently")


# ------------------------------------------------------------------------------------------------------ update_inventory
@handler("update_inventory")
def h_update_inventory(run):
    p = run.p
    pr, ask = _pick_product(run, "change")
    if not pr:
        for e in ask:
            yield e
        run.status = "clarify"
        return
    val = _qty(p)
    if val is None:
        yield msg(f"What value should I set for **{pr['name']}**?")
        run.status = "clarify"
        return
    reorder_mode = p.rx(r"reorder (?:level|point)|reorder")
    wh = db.q1("SELECT * FROM warehouses WHERE id=?", (p.ents["warehouse"][0].id,)) if p.ents.get("warehouse") else None
    params, changes = {"product": pr["name"]}, []
    if reorder_mode:
        params["reorder_level"] = val
        before = db.q1("SELECT SUM(reorder_level) r FROM inventory WHERE product_id=?" + (" AND warehouse_id=?" if wh else ""), (pr["id"],) + ((wh["id"],) if wh else ()))["r"]
        changes.append({"label": f"Reorder level · {pr['name']} · {wh['name'] if wh else 'all warehouses'}", "before": f"{before:,}", "after": f"{val:,}"})
        if wh:
            params["warehouse"] = wh["name"]
    else:
        if not wh:
            whs = [r["name"] for r in db.q("SELECT name FROM warehouses ORDER BY id")]
            yield msg(f"Stock is held per warehouse — in which one should **{pr['name']}** be set to {val:,}? " + ", ".join(f"**{w}**" for w in whs))
            yield chips([f"Set {pr['name']} stock in {w} to {val}" for w in whs])
            run.status = "clarify"
            return
        before = db.scalar("SELECT qty FROM inventory WHERE product_id=? AND warehouse_id=?", (pr["id"], wh["id"]))
        params.update(set_qty=val, warehouse=wh["name"])
        changes.append({"label": f"Stock · {pr['name']} · {wh['name']}", "before": f"{before:,}", "after": f"{val:,}"})
    yield nav("inventory", {"search": pr["name"], **({"warehouse": str(wh["id"])} if wh else {})}, label=pr["name"])
    yield {"t": "ui", "a": "open_form", "form": "stock", "values": {"product": pr["name"], "warehouse": wh["name"] if wh else None, "set_qty": params.get("set_qty"), "reorder_level": params.get("reorder_level")}}
    yield msg("Here is the change I'm about to make — confirm to apply it.")
    yield new_confirmation(run.user, f"Update inventory · {pr['name']}", [("inventory.update", params)], changes=changes, group="inventory.update",
                           after=[{"t": "ui", "a": "close_form"}, nav("inventory", {"search": pr["name"]}, label=pr["name"])], button="Apply change")


# -------------------------------------------------------------------------------------------------- update_order_status
STATUS_WORDS = [(r"\b(cancel\w*)\b", "cancelled"), (r"\b(deliver\w*)\b", "delivered"), (r"\b(ship\w*)\b", "shipped"), (r"\b(process\w*)\b", "processing"), (r"\bpending\b", "pending")]


@handler("update_order_status")
def h_order_status(run):
    p = run.p
    if not p.so_refs:
        yield msg("Which order? Give me an order number like **SO-100234**.")
        run.status = "clarify"
        return
    o = db.q1("SELECT * FROM sales_orders WHERE upper(so_number)=upper(?)", (p.so_refs[0],))
    if not o:
        yield msg(f"I can't find **{p.so_refs[0]}**.")
        run.status = "error"
        return
    new = next((v for pat, v in STATUS_WORDS if re.search(pat, p.t)), None)
    if not new:
        yield msg("What should the new status be: **processing**, **shipped**, **delivered** or **cancelled**?")
        run.status = "clarify"
        return
    if new not in ops.ORDER_FLOW[o["status"]]:
        yield msg(f"**{o['so_number']}** is **{o['status']}**, so it can't move to **{new}**. Allowed next steps: {', '.join(ops.ORDER_FLOW[o['status']]) or 'none (final state)'}.")
        run.status = "refused"
        return
    yield nav("sales", {"search": o["so_number"], "date_range": "last_24_months"}, label=o["so_number"])
    yield msg(f"Ready to move **{o['so_number']}** from **{o['status']}** to **{new}**.")
    yield new_confirmation(run.user, f"{o['so_number']} → {new}", [("order.update_status", {"order": o["so_number"], "status": new})], changes=[{"label": f"{o['so_number']} status", "before": o["status"], "after": new}],
                           group="order.status", risk="destructive" if new == "cancelled" else "write", after=[{"t": "ui", "a": "refresh"}], button="Update status")


# ----------------------------------------------------------------------------------------------------------- suppliers
@handler("add_supplier")
def h_add_supplier(run):
    p = run.p
    m = re.search(r"(?:supplier|vendor)\s+(?:called|named)?\s*((?:[A-Z][\w&.\-]*)(?:\s+[A-Z][\w&.\-]*){0,3})", run.message)
    name = None
    if m:
        name = re.split(r"\s+(?:From|In|For|Based)\b", m.group(1))[0].strip()
    if p.ents.get("supplier"):
        ex = p.ents["supplier"][0]
        yield nav("suppliers", {"search": ex.name}, label=ex.name)
        yield msg(f"**{ex.name}** is already in your supplier list, so I opened it instead of adding a duplicate.")
        return
    country = p.ents["country"][0].name if p.ents.get("country") else None
    if not country:
        m2 = re.search(r"\b(?:from|in|based in)\s+([A-Z][a-zA-Z]+)", run.message)
        country = m2.group(1) if m2 else None
    cat = p.ents["category"][0].id if p.ents.get("category") else None
    values = {k: v for k, v in {"name": name, "country": country, "category": cat}.items() if v}
    yield nav("suppliers", {}, label="Add supplier")
    yield {"t": "ui", "a": "open_form", "form": "supplier", "values": values}
    missing = [k for k in ("name", "country", "category") if k not in values]
    yield msg("I opened the **Add supplier** form" + (f" and filled in {', '.join(f'**{k}**' for k in values)}" if values else "") + "."
              + (f" Still needed: {', '.join(missing)}." if missing else " Review it and press **Add supplier**."))


@handler("update_supplier_status")
def h_supplier_status(run):
    p = run.p
    s = db.q1("SELECT * FROM suppliers WHERE id=?", (p.ents["supplier"][0].id,))
    if p.rx(r"\bhold\b|suspend|freeze"):
        new = "on_hold" if not p.rx(r"inactive|deactivat") else "inactive"
    elif p.rx(r"deactivat|inactive|disable|blacklist"):
        new = "inactive"
    elif p.rx(r"activat|resume|enable|active"):
        new = "active"
    else:
        yield msg(f"What status should **{s['name']}** get: **active**, **on hold** or **inactive**?")
        run.status = "clarify"
        return
    if s["status"] == new:
        yield msg(f"**{s['name']}** is already {new.replace('_', ' ')}.")
        return
    openpo = db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id=? AND status IN ('draft','pending_approval','approved','ordered')", (s["id"],))
    yield nav("suppliers", {"search": s["name"]}, label=s["name"])
    yield msg(f"Ready to set **{s['name']}** to **{new.replace('_', ' ')}**.")
    yield new_confirmation(run.user, f"{s['name']} → {new.replace('_', ' ')}", [("supplier.update", {"supplier": s["name"], "status": new})], changes=[{"label": f"{s['name']} status", "before": s["status"].replace("_", " "), "after": new.replace("_", " ")}],
                           warnings=[f"{openpo} open purchase order(s) with this supplier are not affected, but new POs can't be raised while it isn't active."] if new != "active" and openpo else [], group="supplier.update",
                           after=[{"t": "ui", "a": "refresh"}], button="Apply")
