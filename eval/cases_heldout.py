"""HELD-OUT evaluation set: written before any tuning on the dev set and never used to tune routing rules or exemplars.

Terser, chattier and differently-worded than the exemplars in server/intents.py. Authored by the same person as the
system, so treat the score as an optimistic estimate of real-user performance, not a guarantee.
Placeholders ({po_draft}, {so_pending}, ...) are filled from the live tenant at run time.
"""
from .cases import C

HELDOUT = [
    # --- navigation / UI state
    C("inventory please", "show_view", page="inventory", filters={}),
    C("pull up the supplier list", "show_view", page="suppliers", filters={}),
    C("i wanna see purchase orders", "show_view", page="procurement", filters={}),
    C("low on stock items", "show_view", page="inventory", filters={"status": "low_stock"}),
    C("let me see orders that got cancelled last month", "show_view", page="sales", filters={"status": "cancelled", "date_range": "last_month"}),
    C("suppliers from China", "show_view", page="suppliers", filters={"country": "China"}),
    C("anything waiting for my approval?", "show_view", page="approvals", filters={"status": "pending"}),
    C("european orders this quarter", "show_view", page="sales", filters={"region": "Europe", "date_range": "this_quarter"}),
    C("stock levels sorted by lowest stock", "show_view", page="inventory", filters={"sort": "qty:asc"}),
    C("which shipments are late", "show_view", page="logistics", filters={"status": "delayed"}),
    C("show me finance for ytd", "show_view", page="finance", filters={"date_range": "ytd"}),
    C("pos in draft", "show_view", page="procurement", filters={"status": "draft"}),
    C("open sales and logistics in separate tabs", "show_view", pages=["sales", "logistics"], new_tab=True),
    C("Dallas inventory", "show_view", page="inventory", filters={"warehouse": "2"}),
    C("OceanLink deliveries", "show_view", page="logistics", filters={"carrier": "OceanLink"}),
    C("show me chemicals stock", "show_view", page="inventory", filters={"category": "Chemicals"}),
    C("all shipped orders from latam", "show_view", page="sales", filters={"status": "shipped", "region": "LATAM"}),
    C("take me to the analytics workspace", "show_view", page="analytics", filters={}),
    # --- page awareness / meta
    C("where am i?", "explain_page", ctx_page="procurement"),
    C("describe this screen", "explain_page", ctx_page="finance"),
    C("what are you able to do", "help"),
    C("reset everything on this page", "reset_filters", ctx_page="inventory", ctx_filters={"status": "low_stock"}, page="inventory", filters={}),
    C("hi there", "chitchat"),
    # --- KPI
    C("revenue for last quarter?", "kpi_query", metric="revenue", period="last_quarter"),
    C("tell me the number of orders in august 2026", "kpi_query", metric="orders", period="2026-08-01..2026-08-31"),
    C("how much stock value do we hold", "kpi_query", metric="inventory_value"),
    C("what is the profit for q1 2026", "kpi_query", metric="profit", period="2026-01-01..2026-03-31"),
    C("what percent of orders were cancelled last quarter", "kpi_query", metric="cancel_rate", period="last_quarter"),
    C("what's our on-time rate for last month", "kpi_query", metric="on_time_rate", period="last_month"),
    C("how many suppliers are active", "kpi_query", metric="active_suppliers"),
    # --- trend / compare / explain
    C("revenue trend please", "trend", metric="revenue"),
    C("how did sales develop over the last 12 months", "trend", metric="revenue", period="last_12_months"),
    C("orders month by month for 2025", "trend", metric="orders", period="2025-01-01..2025-12-31"),
    C("revenue this month versus last month", "compare_periods", metric="revenue"),
    C("2025 vs 2026 profit", "compare_periods", metric="profit"),
    C("how are we doing against last year in terms of orders", "compare_periods", metric="orders"),
    C("why did orders fall in march 2026", "explain_change", metric="orders", period="2026-03-01..2026-03-31"),
    C("what happened to revenue last month", "explain_change", metric="revenue", period="last_month"),
    C("reason for lower profit in q3", "explain_change", metric="profit", period="2026-07-01..2026-09-30"),
    # --- ranking / forecast / suppliers / lookup / report
    C("top ten products", "top_n", n=10, ents={}),
    C("who are the biggest customers in europe", "top_n", ents={"region": "Europe"}),
    C("which regions sell the most", "top_n"),
    C("least selling products in 2025", "top_n", period="2025-01-01..2025-12-31"),
    C("best 3 suppliers by rating", "top_n", n=3),
    C("what's gonna run out of stock in 10 days", "stockout_forecast", horizon=10),
    C("any shortages coming up", "stockout_forecast"),
    C("cheapest vendor for ball bearing", "compare_suppliers", ents={"product": "Ball Bearing"}),
    C("who sells corrugated boxes at the best price", "compare_suppliers", ents={"product": "Corrugated Box"}),
    C("compare suppliers for stainless steel pipe by reliability", "compare_suppliers", ents={"product": "Stainless Steel Pipe"}),
    C("info on VoltEdge", "entity_lookup", ents={"supplier": "VoltEdge"}),
    C("what is the stock level of ball bearings", "entity_lookup", ents={"product": "Ball Bearing"}),
    C("{po_pending} details", "entity_lookup"),
    C("business review for last month", "generate_report", period="last_month"),
    C("monthly report for august", "generate_report", period="2026-08-01..2026-08-31"),
    # --- guarded writes
    C("need 1000 pcs of ball bearing from the cheapest supplier", "create_po", write=True, ents={"product": "Ball Bearing"}),
    C("po for 250 steel rod from steel supplies", "create_po", write=True, ents={"product": "Steel Rod", "supplier": "Steel Supplies Co."}),
    C("reorder all low stock products", "create_po_bulk", write=True),
    C("release all my draft pos", "submit_po", write=True),
    C("approve everything under 20k that's pending", "approve_po", write=True),
    C("{po_ordered} has arrived at Dallas DC", "receive_po", write=True),
    C("remove {po_draft}", "delete_po", write=True),
    C("make reorder point for brass fittings 600", "update_inventory", write=True, ents={"product": "Brass Fittings"}),
    C("ship order {so_processing}", "update_order_status", write=True),
    C("freeze Prime Plastics", "update_supplier_status", write=True, ents={"supplier": "Prime Plastics"}),
    C("onboard supplier Zenith Packaging in Mexico", "add_supplier", write=True),
    # --- out of scope: must not navigate, plan writes, or invent
    C("what is 2+2", "none"),
    C("book me a flight to Berlin", "none"),
    C("email all suppliers about the delay", "none"),
    C("wipe the inventory table", "none"),
    C("pay supplier invoice for 20000 dollars", "none"),
]
