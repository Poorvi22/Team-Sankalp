"""FRESH held-out set (v2): written after the first tuning round, run once, and NOT used for tuning.

Different entities, periods and phrasing from dev / held-out v1, including deliberately hard cases (typo, indirect wording,
idioms). Same caveat: same author as the system. Failures here are reported, not patched away.
"""
from .cases import C

HELDOUT2 = [
    # --- navigation / UI state
    C("can you open the suppliers screen for me", "show_view", page="suppliers", filters={}),
    C("jump to finance, last 90 days", "show_view", page="finance", filters={"date_range": "last_90_days"}),
    C("list every purchase order that is still pending approval", "show_view", page="procurement", filters={"status": "pending_approval"}),
    C("inventory in Singapore that's out of stock", "show_view", page="inventory", filters={"warehouse": "4", "status": "out_of_stock"}),
    C("I'd like to see cancelled purchase orders from Titan Alloys", "show_view", page="procurement", filters={"status": "cancelled", "supplier": "Titan Alloys"}),
    C("sales for Packaging in APAC during the last 3 months", "show_view", page="sales", filters={"category": "Packaging", "region": "APAC", "date_range": "2026-08-01..2026-10-10"}),
    C("show shipped orders newest first", "show_view", page="sales", filters={"status": "shipped", "sort": "order_date:desc"}),
    C("suppliers in Mexico with a rating of 4.5 or more", "show_view", page="suppliers", filters={"country": "Mexico", "rating_min": "4.5"}),
    C("put the logistics and finance pages in tabs", "show_view", pages=["logistics", "finance"], new_tab=True),
    C("what are my approved purchase orders", "show_view", page="procurement", filters={"status": "approved"}),
    C("delayed deliveries from FastFreight", "show_view", page="logistics", filters={"status": "delayed", "carrier": "FastFreight"}),
    C("ok now show only the delivered ones", "show_view", page="sales", filters={"status": "delivered"}, ctx_page="sales"),
    C("low stock electrical items", "show_view", page="inventory", filters={"status": "low_stock", "category": "Electrical"}),
    C("orders placed in March 2026", "show_view", page="sales", filters={"date_range": "2026-03-01..2026-03-31"}),
    C("go back to the main dashboard", "show_view", page="dashboard", filters={}, ctx_page="sales"),
    C("show me stock for PVC Pipe", "show_view", page="inventory", filters={"search": "PVC Pipe"}),
    C("sort suppliers by on time performance", "show_view", page="suppliers", filters={"sort": "on_time_pct:desc"}),
    C("procurment page", "show_view", page="procurement", filters={}),                       # typo: known weak spot
    # --- page awareness / meta
    C("what am I looking at right now?", "explain_page", ctx_page="inventory"),
    C("can you walk me through this page", "explain_page", ctx_page="suppliers"),
    C("what are you good for", "help"),
    C("start fresh", "reset_filters", ctx_page="sales", ctx_filters={"status": "cancelled"}, page="sales", filters={}),
    # --- KPI
    C("revenue in Q4 2025?", "kpi_query", metric="revenue", period="2025-10-01..2025-12-31"),
    C("number of delayed shipments last quarter", "kpi_query", metric="delayed_shipments", period="last_quarter"),
    C("how much did we spend on procurement in 2025", "kpi_query", metric="po_spend", period="2025-01-01..2025-12-31"),
    C("current inventory worth", "kpi_query", metric="inventory_value"),
    C("what was our profit in the last 30 days", "kpi_query", metric="profit", period="last_30_days"),
    C("how many orders were placed last month", "kpi_query", metric="orders", period="last_month"),
    C("what is the average order value in APAC for Q3", "kpi_query", metric="avg_order_value", period="2026-07-01..2026-09-30", ents={"region": "APAC"}),
    # --- trend / compare / explain
    C("chart orders over the last 6 months", "trend", metric="orders", period="last_6_months"),
    C("is profit going up or down over time", "trend", metric="profit"),
    C("put this quarter next to last quarter for revenue", "compare_periods", metric="revenue"),
    C("orders: Q1 2026 versus Q1 2025", "compare_periods", metric="orders"),
    C("how does last month stack up against the month before for expenses", "compare_periods", metric="expenses"),
    C("what's behind the drop in on-time delivery in March 2026", "explain_change", metric="on_time_rate", period="2026-03-01..2026-03-31"),
    C("why are expenses up this quarter", "explain_change", metric="expenses", period="this_quarter"),
    C("explain why cancellations went up last quarter", "explain_change", metric="cancelled_orders", period="last_quarter"),
    # --- ranking
    C("who are our top 3 customers by revenue last quarter", "top_n", n=3, period="last_quarter"),
    C("rank product categories by sales this year", "top_n", period="ytd"),
    C("which warehouse ships the most orders", "top_n"),
    C("weakest suppliers by on time delivery", "top_n"),
    C("five best-selling products in europe", "top_n", n=5, ents={"region": "Europe"}),
    # --- forecast
    C("what could we run short of before the end of the month", "stockout_forecast"),
    C("which items will hit zero within 3 weeks", "stockout_forecast", horizon=21),
    C("products about to stock out in the next 5 days", "stockout_forecast", horizon=5),
    # --- suppliers / lookup / report
    C("who has the best price on PVC pipe", "compare_suppliers", ents={"product": "PVC Pipe"}),
    C("find a reliable vendor for servo motors", "compare_suppliers", ents={"product": "Servo Motor"}),
    C("tell me what you know about Titan Alloys", "entity_lookup", ents={"supplier": "Titan Alloys"}),
    C("how much Welding Flux do we have", "entity_lookup", ents={"product": "Welding Flux"}),
    C("give me the rundown on {customer1}", "entity_lookup", ents={"customer": "{customer1}"}),
    C("prepare a performance report for Q2", "generate_report", period="2026-04-01..2026-06-30"),
    C("snapshot of the business this year", "generate_report", period="ytd"),
    # --- guarded writes
    C("please place an order for 750 units of Corrugated Box with {supplier_for_box}", "create_po", write=True, ents={"product": "Corrugated Box", "supplier": "{supplier_for_box}"}),
    C("we're out of Packing Tape, order 300 rolls", "create_po", write=True, ents={"product": "Packing Tape"}),
    C("top up everything that's below its reorder level", "create_po_bulk", write=True),
    C("send my drafts off for approval", "submit_po", write=True),
    C("approve {po_pending} please", "approve_po", write=True),
    C("turn down {po_pending}", "approve_po", write=True),
    C("log {po_ordered} as received in Chicago DC", "receive_po", write=True),
    C("get rid of draft {po_draft}", "delete_po", write=True),
    C("bump the Welding Flux reorder level to 250", "update_inventory", write=True, ents={"product": "Welding Flux"}),
    C("flip {so_processing} to shipped", "update_order_status", write=True),
    C("suspend VoltEdge", "update_supplier_status", write=True, ents={"supplier": "VoltEdge"}),
    # --- out of scope
    C("tell me a joke", "none"),
    C("who won the game last night", "none"),
    C("email the board a summary", "none"),
    C("delete every supplier", "none"),
    C("can you pay supplier Titan Alloys $5000", "none"),
    C("refund customer order {so_delivered}", "none"),
]
