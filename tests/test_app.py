"""Test suite:  python -m unittest discover -s tests -v      (seeds its own temporary database; never touches data/)"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="bizpilot_test_")
os.environ["BIZPILOT_DB"] = str(Path(_TMP) / "test.db")

from fastapi.testclient import TestClient   # noqa: E402

from server import agent, agentkit, db, intents, llm, nlu, ops, pages, periods as P, seed   # noqa: E402

AS_OF = date(2026, 10, 10)


def setUpModule():
    seed.seed(as_of=AS_OF, verbose=False)
    db.get_db()


def user(email="michael@bizpilot.demo"):
    return db.q1("SELECT * FROM users WHERE email=?", (email,))


def turn(text, page="dashboard", filters=None):
    return list(agent.run_turn(user(), text, {"page": page, "filters": filters or {}, "_noaudit": True}))


def kinds(evs):
    return [e["t"] for e in evs]


class TestPeriods(unittest.TestCase):
    def test_named_and_relative(self):
        a = lambda t: [(p.start.isoformat(), p.end.isoformat()) for p in P.parse_periods(t, AS_OF)]
        self.assertEqual(a("revenue last quarter"), [("2026-07-01", "2026-09-30")])
        self.assertEqual(a("orders in March 2026"), [("2026-03-01", "2026-03-31")])
        self.assertEqual(a("Q2 vs Q3"), [("2026-04-01", "2026-06-30"), ("2026-07-01", "2026-09-30")])
        self.assertEqual(a("2025 vs 2026"), [("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-10-10")])
        self.assertEqual(a("what may happen"), [])                  # modal verb, not the month
        self.assertEqual(a("in may"), [("2026-05-01", "2026-05-31")])

    def test_rolling_ranges_include_today(self):
        p = P.preset("last_12_months", AS_OF)
        self.assertEqual((p.start.isoformat(), p.end.isoformat()), ("2025-11-01", "2026-10-10"))

    def test_previous_like_for_like(self):
        mtd = P.preset("this_month", AS_OF)
        prev = P.previous(mtd, AS_OF)
        self.assertEqual((prev.start.isoformat(), prev.end.isoformat()), ("2026-09-01", "2026-09-10"))


class TestNLU(unittest.TestCase):
    def test_entities_are_grounded_in_the_database(self):
        p = nlu.parse("create a PO for 500 Steel Rod from Global Metals")
        self.assertEqual(p.ents["product"][0].name, "Steel Rod")
        self.assertEqual(p.ents["supplier"][0].name, "Global Metals Ltd.")
        self.assertEqual(p.numbers[0][0], 500)
        self.assertEqual(nlu.delex(p), "create a po for numtok entproduct from entsupplier")

    def test_partial_product_name_becomes_a_group(self):
        p = nlu.parse("compare suppliers for steel")
        self.assertEqual(p.product_group[0], "steel")
        self.assertGreaterEqual(len(p.product_group[1]), 2)

    def test_unknown_names_are_not_invented(self):
        p = nlu.parse("order 10 Unobtainium Rods from Acme Corp")
        self.assertFalse(p.ents.get("product") or p.ents.get("supplier"))        # nothing is linked to a record that does not exist
        evs = turn("order 10 Unobtainium Rods from Acme Corp")                  # 'rod' is a real word -> the agent asks which product, it does not guess
        self.assertNotIn("confirm", kinds(evs))
        self.assertFalse(any(e.get("a") == "open_form" for e in evs if e["t"] == "ui"))


class TestRouting(unittest.TestCase):
    CASES = [("show low stock items", "show_view"), ("what is total revenue last quarter", "kpi_query"), ("compare revenue this month vs last month", "compare_periods"),
             ("why did revenue drop in March 2026", "explain_change"), ("top 5 products by revenue", "top_n"), ("which products are likely to run out in 14 days", "stockout_forecast"),
             ("compare top 3 suppliers for steel with best pricing", "compare_suppliers"), ("create purchase orders for all low stock items", "create_po_bulk"),
             ("what page am I on", "explain_page"), ("delete all customers", "unsupported")]

    def test_intents(self):
        for text, want in self.CASES:
            with self.subTest(text):
                route = next(e for e in turn(text) if e["t"] == "route")
                self.assertEqual(route["intent"], want)

    def test_out_of_scope_asks_instead_of_guessing(self):
        evs = turn("flibber the gronk")
        route = next(e for e in evs if e["t"] == "route")
        self.assertIsNone(route["intent"])
        self.assertNotIn("ui", kinds(evs))
        self.assertNotIn("confirm", kinds(evs))

    def test_navigation_state_is_exact(self):
        nav = next(e for e in turn("show cancelled orders for last quarter") if e["t"] == "ui")
        self.assertEqual((nav["page"], nav["filters"]), ("sales", {"status": "cancelled", "date_range": "last_quarter"}))
        clean, rejected = ops.validate_filters(nav["page"], nav["filters"])
        self.assertEqual(rejected, {})

    def test_followup_inherits_slots(self):
        turn("compare revenue this month vs last month")
        evs = turn("and for Europe?", page="analytics")
        route = next(e for e in evs if e["t"] == "route")
        self.assertEqual(route["intent"], "compare_periods")
        self.assertTrue(route["followup"])
        self.assertEqual(route["slots"]["ents"]["region"], ["Europe"])


class TestGuardrails(unittest.TestCase):
    WRITES = ["create purchase orders for all low stock items", "create a purchase order for 500 steel rods from Global Metals", "submit all draft purchase orders",
              "approve all pending purchase orders", "adjust steel rod stock in Chicago to 1200", "put Prime Plastics on hold", "delete all draft POs"]

    def test_no_write_without_confirmation(self):
        for text in self.WRITES:
            with self.subTest(text):
                v0 = db.data_version()
                evs = turn(text, page="procurement")
                self.assertEqual(db.data_version(), v0, "the turn itself must not write")
                self.assertTrue("confirm" in kinds(evs) or any(e.get("a") == "open_form" for e in evs if e["t"] == "ui") or evs[-1]["meta"]["status"] in ("refused", "clarify"))

    def test_confirmation_is_bound_to_the_user_and_single_use(self):
        evs = turn("put Prime Plastics on hold")
        token = next(e for e in evs if e["t"] == "confirm")["token"]
        other = user("priya@bizpilot.demo")
        res = list(agent.confirm(other, token, "approve"))
        self.assertIn("expired", res[0]["text"])
        res = list(agent.confirm(user(), token, "approve"))        # token was consumed by the failed attempt? no: popped -> expired
        self.assertTrue(res)

    def test_cancel_changes_nothing(self):
        before = db.scalar("SELECT COUNT(*) FROM suppliers WHERE status='on_hold'")
        token = next(e for e in turn("put Steel Supplies Co. on hold") if e["t"] == "confirm")["token"]
        list(agent.confirm(user(), token, "cancel"))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM suppliers WHERE status='on_hold'"), before)

    def test_delete_only_drafts(self):
        ordered = db.q1("SELECT po_number FROM purchase_orders WHERE status IN ('received','ordered') LIMIT 1")["po_number"]
        evs = turn(f"delete {ordered}")
        self.assertNotIn("confirm", kinds(evs))
        with self.assertRaises(ops.OpError):
            ops.call("po.delete", user(), {"po": ordered})

    def test_approval_limit_by_role(self):
        big = db.q1("SELECT po_number FROM purchase_orders WHERE total>100000 LIMIT 1")
        if big:
            db.get_db().execute("UPDATE purchase_orders SET status='pending_approval' WHERE po_number=?", (big["po_number"],)); db.get_db().commit(); db.bump()
            with self.assertRaises(ops.OpError):
                ops.call("po.approve", user("michael@bizpilot.demo"), {"po": big["po_number"]})
            self.assertEqual(ops.call("po.approve", user("priya@bizpilot.demo"), {"po": big["po_number"]})["status"], "approved")

    def test_po_over_50k_goes_to_approval_queue(self):
        r = ops.call("po.create", user(), {"supplier": "Global Metals Ltd.", "items": [{"product": "Steel Rod", "qty": 3000}]})
        self.assertGreater(r["po"]["total"], 50000)
        self.assertEqual(ops.call("po.submit", user(), {"po": r["po"]["po_number"]})["status"], "pending_approval")

    def test_ops_validate_inputs(self):
        with self.assertRaises(ops.OpError):
            ops.call("po.create", user(), {"supplier": "Nobody Ltd", "items": [{"product": "Steel Rod", "qty": 5}]})
        with self.assertRaises(ops.OpError):
            ops.call("inventory.update", user(), {"product": "Steel Rod", "warehouse": "Chicago DC", "set_qty": -5})


class TestAnalytics(unittest.TestCase):
    def test_numbers_match_independent_sql(self):
        per = P.preset("last_quarter", AS_OF)
        truth = db.scalar("SELECT SUM(i.qty*i.unit_price) FROM sales_orders o JOIN so_items i ON i.order_id=o.id WHERE o.status!='cancelled' AND o.order_date BETWEEN ? AND ?",
                          (per.start.isoformat(), per.end.isoformat()))
        card = next(e["card"] for e in turn("what is total revenue last quarter") if e["t"] == "card")
        self.assertAlmostEqual(card["value"], truth, delta=truth * 0.005)

    def test_planted_disruption_is_discovered_not_hardcoded(self):
        evs = turn("why did revenue drop in March 2026")
        bullets = " ".join(b["text"] for e in evs if e["t"] == "card" and e["card"]["kind"] == "analysis" for b in e["card"]["bullets"])
        self.assertIn("Metals", bullets)
        self.assertIn("days late", bullets)

    def test_partial_month_excluded_from_trend_statistics(self):
        pts = __import__("server.analytics", fromlist=["x"]).series("revenue", date(2026, 1, 1), AS_OF)
        self.assertTrue(pts[-1].get("partial"))
        st = __import__("server.analytics", fromlist=["x"]).trend_stats(pts)
        self.assertEqual(st["n"], len(pts) - 1)


class TestLLMTier(unittest.TestCase):
    def test_route_validates_model_output(self):
        good = {"content": [{"type": "tool_use", "name": "route_request", "input": {"intent": "stockout_forecast", "rewrite": "which products will run out in 30 days"}}]}
        bad_intent = {"content": [{"type": "tool_use", "name": "route_request", "input": {"intent": "launch_missiles", "rewrite": "x"}}]}
        self.assertEqual(llm.route("anything", "dashboard", "2026-10-10", intents.INTENTS, transport=lambda *a: good)["intent"], "stockout_forecast")
        self.assertIsNone(llm.route("anything", "dashboard", "2026-10-10", intents.INTENTS, transport=lambda *a: bad_intent))
        self.assertIsNone(llm.route("anything", "dashboard", "2026-10-10", intents.INTENTS, transport=lambda *a: (_ for _ in ()).throw(OSError("network down"))))

    def test_unsure_requests_go_through_the_same_guarded_pipeline(self):
        orig = (llm.enabled, llm.route)
        llm.enabled = lambda: True
        llm.route = lambda *a, **k: {"intent": "stockout_forecast", "rewrite": "which products will run out in 30 days"}
        try:
            evs = turn("which items will hit zero within 3 weeks")      # a phrase the fast path does not understand
            route = next(e for e in evs if e["t"] == "route")
            self.assertEqual((route["tier"], route["intent"]), ("llm", "stockout_forecast"))
            self.assertEqual(route["slots"]["horizon_days"], 30)
            self.assertIn("card", kinds(evs))
        finally:
            llm.enabled, llm.route = orig

    def test_llm_cannot_bypass_confirmation(self):
        orig = (llm.enabled, llm.route)
        llm.enabled = lambda: True
        llm.route = lambda *a, **k: {"intent": "delete_po", "rewrite": "delete all draft POs"}
        try:
            v0 = db.data_version()
            evs = turn("please make the paperwork vanish")
            self.assertEqual(db.data_version(), v0)
            self.assertIn("confirm", kinds(evs))
        finally:
            llm.enabled, llm.route = orig


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_requires_login(self):
        c = TestClient(__import__("server.main", fromlist=["app"]).app)
        self.assertEqual(c.get("/api/meta").status_code, 401)
        self.assertEqual(c.post("/api/auth/login", json={"email": "michael@bizpilot.demo", "password": "wrong"}).status_code, 401)

    def test_every_page_builds(self):
        self.client.post("/api/auth/login", json={"email": "michael@bizpilot.demo", "password": "demo1234"})
        meta = self.client.get("/api/meta").json()
        self.assertEqual(len(meta["pages"]), 10)
        for pg in meta["pages"]:
            r = self.client.get(f"/api/page?page={pg['id']}")
            self.assertEqual(r.status_code, 200, pg["id"])
            self.assertTrue(all(w["data"] is not None for w in r.json()["widgets"]), pg["id"])

    def test_agent_stream_ends_with_done(self):
        self.client.post("/api/auth/login", json={"email": "michael@bizpilot.demo", "password": "demo1234"})
        with self.client.stream("POST", "/api/agent/chat", json={"message": "top 5 products by revenue", "context": {"page": "dashboard"}}) as r:
            evs = [json.loads(l) for l in r.iter_lines() if l]
        self.assertEqual(evs[0]["t"], "route")
        self.assertEqual(evs[-1]["t"], "done")

    def test_rest_ops_reject_bad_input(self):
        self.client.post("/api/auth/login", json={"email": "michael@bizpilot.demo", "password": "demo1234"})
        r = self.client.post("/api/procurement/orders", json={"supplier": "Global Metals Ltd.", "items": [{"product": "Steel Rod", "qty": 0}]})
        self.assertEqual(r.status_code, 400)
        r = self.client.delete("/api/procurement/orders", params={"po": "PO-0000-0000"})
        self.assertIn(r.status_code, (404, 400))


class TestMetadataIndex(unittest.TestCase):
    def test_find_the_right_api_and_component(self):
        top = lambda q: agentkit.find_components(q, 1)[0]["id"]
        self.assertEqual(top("api to create a purchase order"), "op:po.create")
        self.assertEqual(top("limit shipments to delayed ones"), "filter:logistics.status")
        self.assertIn(top("where can I see overdue customer invoices"), {"widget:finance.aging", "page:finance", "widget:finance.top_debtors"})


if __name__ == "__main__":
    unittest.main()
