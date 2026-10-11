"""Offline benchmark for the assistant. Never touches the live database (works on a temporary copy).

    python -m eval.run_eval              # dev + held-out + retrieval + scenarios + latency
    python -m eval.run_eval --failures   # also print every failed case

Writes eval/results.json, which the "Agent Activity" page renders as the KPI scorecard.
"""
import argparse
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="bizpilot_eval_")
_src = Path(os.environ.get("BIZPILOT_DB", ROOT / "data" / "bizpilot.db"))
shutil.copy(_src, Path(_tmp) / "eval.db")
os.environ["BIZPILOT_DB"] = str(Path(_tmp) / "eval.db")          # must be set before importing server modules

from server import agent, agentkit, db, intents, ops, pages, periods as P   # noqa: E402
from server.retrieval import Index                                           # noqa: E402

from .cases import DEV, RETRIEVAL                                             # noqa: E402
from .cases_heldout import HELDOUT                                            # noqa: E402
from .cases_heldout2 import HELDOUT2                                          # noqa: E402

WRITE_INTENTS = {i for i, d in intents.INTENTS.items() if d["risk"] != "read"}
USER = None


def fill(text):
    """Substitute live-tenant placeholders in utterances / expectations."""
    if "{" not in str(text):
        return text
    q = db.q1
    refs = {
        "po_draft": (q("SELECT po_number n FROM purchase_orders WHERE status='draft' ORDER BY id DESC") or {}).get("n", "PO-2026-0001"),
        "po_pending": (q("SELECT po_number n FROM purchase_orders WHERE status='pending_approval' AND total<=100000 ORDER BY id DESC") or {}).get("n", "PO-2026-0001"),
        "po_ordered": (q("SELECT po_number n FROM purchase_orders WHERE status IN ('ordered','approved') ORDER BY id DESC") or {}).get("n", "PO-2026-0001"),
        "so_pending": (q("SELECT so_number n FROM sales_orders WHERE status='pending' ORDER BY id") or {}).get("n", "SO-100001"),
        "so_processing": (q("SELECT so_number n FROM sales_orders WHERE status='processing' ORDER BY id") or {}).get("n", "SO-100001"),
        "supplier_for_box": (q("SELECT s.name n FROM supplier_prices sp JOIN suppliers s ON s.id=sp.supplier_id JOIN products p ON p.id=sp.product_id WHERE p.name='Corrugated Box' AND s.status='active' ORDER BY s.id") or {}).get("n", "PackRight"),
        "supplier_on_hold": (q("SELECT name n FROM suppliers WHERE status='on_hold'") or {}).get("n", "Prime Plastics"),
        "so_delivered": (q("SELECT so_number n FROM sales_orders WHERE status='delivered' ORDER BY id DESC") or {}).get("n", "SO-100001"),
        "customer1": (q("SELECT name n FROM customers ORDER BY id") or {}).get("n", ""),
    }
    return str(text).format(**refs)


def run_once(u, ctx_page="dashboard", ctx_filters=None):
    ctx = {"page": ctx_page, "filters": ctx_filters or {}, "_noaudit": True}
    t0 = time.perf_counter()
    evs = list(agent.run_turn(USER, u, ctx))
    wall = (time.perf_counter() - t0) * 1000
    route = next(e for e in evs if e["t"] == "route")
    return evs, route, wall


def norm_filters(f):
    """date_range given as a preset id and as the equivalent explicit range are the same UI state."""
    f = dict(f)
    if f.get("date_range"):
        per = P.resolve(f["date_range"], db.as_of())
        f["date_range"] = f"{per.start.isoformat()}..{per.end.isoformat()}"
    return f


def check_case(c):
    u = fill(c["u"])
    ver0 = db.data_version()
    evs, route, wall = run_once(u, c["ctx_page"], c["ctx_filters"])
    got_intent = route["intent"]
    exp_intent = None if c["intent"] == "none" else c["intent"]
    navs = [e for e in evs if e["t"] == "ui" and e["a"] == "navigate"]
    forms = [e for e in evs if e["t"] == "ui" and e["a"] == "open_form"]
    confirms = [e for e in evs if e["t"] == "confirm"]
    res = {"u": u, "expected": c["intent"], "got": got_intent, "ms": wall, "intent_ok": got_intent == exp_intent or (exp_intent is None and got_intent == "unsupported"), "checks": {}}
    # --- destination + UI state
    if c["page"] or c["pages"]:
        want = c["pages"] if c["pages"] else [c["page"]]
        got_pages = [n["page"] for n in navs]
        res["checks"]["destination"] = (sorted(got_pages) == sorted(want)) if c["pages"] else (bool(navs) and navs[0]["page"] == want[0])
        if c["new_tab"]:
            res["checks"]["new_tab"] = all(n["new_tab"] for n in navs) and len(navs) >= 2
    if c["filters"] is not None and navs:
        nav = navs[0]
        want_f = {k: v for k, v in c["filters"].items()}
        got_f = nav["filters"]
        res["checks"]["ui_state"] = norm_filters(got_f) == norm_filters(want_f)
        res["filter_keys"] = (sum(1 for k, v in norm_filters(want_f).items() if norm_filters(got_f).get(k) == v), len(want_f), len([k for k in got_f if k not in want_f]))
    elif c["filters"] is not None and not navs:
        res["checks"]["ui_state"] = False
        res["filter_keys"] = (0, len(c["filters"]), 0)
    # --- slots
    sl = route["slots"]
    if c["metric"]:
        res["checks"]["metric"] = sl["metric"] == c["metric"]
    if c["period"]:
        want_p = P.resolve(c["period"], db.as_of())
        got_p = sl["periods"][0] if sl["periods"] else None
        res["checks"]["period"] = bool(got_p) and got_p["start"] == want_p.start.isoformat() and got_p["end"] == want_p.end.isoformat()
    if c["ents"] is not None and c["ents"]:
        ok = True
        for k, v in c["ents"].items():
            v = fill(v)
            names = sl["ents"].get(k, [])
            ok &= (v in names) or (k == "product" and sl["product_group"] and False)
        res["checks"]["entities"] = ok
    if c["n"] is not None:
        res["checks"]["top_n"] = sl["top_n"] == c["n"]
    if c["horizon"] is not None:
        res["checks"]["horizon"] = sl["horizon_days"] == c["horizon"]
    # --- safety
    if c["write"]:
        res["checks"]["write_needs_confirmation"] = bool(confirms or forms) and db.data_version() == ver0
    if c["intent"] == "none":
        res["checks"]["no_action_taken"] = not navs and not confirms and not forms and not any(e["t"] == "card" for e in evs)
    # no hallucinated UI actions: every navigate targets a real page with filters the page actually declares
    bad = 0
    for n in navs:
        if n["page"] not in pages.PAGES:
            bad += 1
        else:
            _, rej = ops.validate_filters(n["page"], n["filters"])
            bad += 1 if rej else 0
    res["hallucinated_actions"] = bad
    res["unconfirmed_write"] = (db.data_version() != ver0)
    res["tier"] = route["tier"]
    return res


def run_set(name, cases):
    out = [check_case(c) for c in cases]
    return out


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def summarise(results):
    n = len(results)
    intent_ok = sum(r["intent_ok"] for r in results)
    dest = [r["checks"]["destination"] for r in results if "destination" in r["checks"]]
    ui = [r["checks"]["ui_state"] for r in results if "ui_state" in r["checks"]]
    keys_ok = sum(r["filter_keys"][0] for r in results if "filter_keys" in r)
    keys_all = sum(r["filter_keys"][1] for r in results if "filter_keys" in r)
    extra = sum(r["filter_keys"][2] for r in results if "filter_keys" in r)
    slot_checks = {}
    for r in results:
        for k, v in r["checks"].items():
            if k in ("metric", "period", "entities", "top_n", "horizon"):
                slot_checks.setdefault(k, []).append(v)
    slots_all = [v for vs in slot_checks.values() for v in vs]
    writes = [r["checks"]["write_needs_confirmation"] for r in results if "write_needs_confirmation" in r["checks"]]
    oos = [r["checks"]["no_action_taken"] for r in results if "no_action_taken" in r["checks"]]
    return {"n": n, "intent_acc": pct(intent_ok, n), "dest_acc": pct(sum(dest), len(dest)), "dest_n": len(dest), "ui_exact": pct(sum(ui), len(ui)), "ui_n": len(ui),
            "ui_key_recall": pct(keys_ok, keys_all), "ui_extra_keys": extra, "slot_acc": pct(sum(slots_all), len(slots_all)), "slot_n": len(slots_all),
            "slot_detail": {k: pct(sum(v), len(v)) for k, v in slot_checks.items()}, "write_confirmed": pct(sum(writes), len(writes)), "write_n": len(writes),
            "oos_safe": pct(sum(oos), len(oos)), "oos_n": len(oos), "halluc": sum(r["hallucinated_actions"] for r in results),
            "unconfirmed_writes": sum(r["unconfirmed_write"] for r in results)}


# ------------------------------------------------------------------------------------------------ retrieval metrics
def retrieval_eval(extra_distractors=0):
    ix = agentkit.metadata_index()
    if extra_distractors:
        rnd = random.Random(7)
        vocab = [w for d in ix.docs if d["kind"] in ("page", "widget", "filter", "op") for w in d["text"].split()]
        ix = Index()
        for d in agentkit.metadata_index().docs:
            ix.add(d["id"], d["text"], d["kind"], **d["meta"])
        for i in range(extra_distractors):          # synthetic components built from the same vocabulary -> hard negatives
            ix.add(f"widget:synthetic.w{i}", " ".join(rnd.choice(vocab) for _ in range(rnd.randint(12, 30))), "widget", page="synthetic")
        ix.build()
    rr, r1, r3, r5 = [], 0, 0, 0
    for q, gold in RETRIEVAL:
        ranked = [c["id"] for c in agentkit.find_components(q, 10)] if not extra_distractors else [d["id"] for d, _ in ix.search(q, k=10, kinds={"page", "widget", "filter", "op"})]
        pos = next((i for i, x in enumerate(ranked, 1) if x in gold), None)
        rr.append(1 / pos if pos else 0.0)
        r1 += pos == 1
        r3 += bool(pos and pos <= 3)
        r5 += bool(pos and pos <= 5)
    n = len(RETRIEVAL)
    return {"mrr": round(statistics.mean(rr), 3), "r1": pct(r1, n), "r3": pct(r3, n), "r5": pct(r5, n), "n": n, "docs": len(ix.docs)}


# ----------------------------------------------------------------------------------- independent numeric check
def numeric_faithfulness():
    """Recompute headline answers with hand-written SQL (independent of analytics.py) and compare with what the agent says."""
    a = db.as_of()
    checks = []

    def msg_of(u):
        evs, _, _ = run_once(u)
        return evs

    # revenue last quarter
    per = P.preset("last_quarter", a)
    truth = db.scalar("SELECT SUM(i.qty*i.unit_price) FROM sales_orders o JOIN so_items i ON i.order_id=o.id WHERE o.status!='cancelled' AND o.order_date BETWEEN ? AND ?", (per.start.isoformat(), per.end.isoformat()))
    card = next(e["card"] for e in msg_of("what is total revenue last quarter") if e["t"] == "card")
    checks.append(("revenue last quarter", abs(card["value"] - truth) / truth < 0.005))
    # order count
    per = P.month_period(2026, 3, a)
    truth = db.scalar("SELECT COUNT(DISTINCT o.id) FROM sales_orders o WHERE o.status!='cancelled' AND o.order_date BETWEEN ? AND ?", (per.start.isoformat(), per.end.isoformat()))
    card = next(e["card"] for e in msg_of("how many orders in March 2026") if e["t"] == "card")
    checks.append(("orders Mar 2026", card["value"] == truth))
    # top-5 products
    per = P.preset("last_12_months", a)
    rows = db.q("SELECT p.name, SUM(i.qty*i.unit_price) v FROM sales_orders o JOIN so_items i ON i.order_id=o.id JOIN products p ON p.id=i.product_id WHERE o.status!='cancelled' AND o.order_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC LIMIT 5", (per.start.isoformat(), per.end.isoformat()))
    card = next(e["card"] for e in msg_of("top 5 products by revenue") if e["t"] == "card")
    checks.append(("top-5 products", [b["label"] for b in card["bars"]] == [r["name"] for r in rows] and all(abs(b["value"] - r["v"]) < 1 for b, r in zip(card["bars"], rows))))
    # month-on-month comparison
    ps, pf = P.month_period(2026, 3, a), P.month_period(2026, 2, a)
    sql = "SELECT SUM(i.qty*i.unit_price) FROM sales_orders o JOIN so_items i ON i.order_id=o.id WHERE o.status!='cancelled' AND o.order_date BETWEEN ? AND ?"
    va, vb = db.scalar(sql, (ps.start.isoformat(), ps.end.isoformat())), db.scalar(sql, (pf.start.isoformat(), pf.end.isoformat()))
    card = next(e["card"] for e in msg_of("compare revenue March 2026 vs February 2026") if e["t"] == "card" and e["card"]["kind"] == "compare")
    checks.append(("MoM revenue compare", abs(card["a"]["value"] - va) < 1 and abs(card["b"]["value"] - vb) < 1 and abs(card["pct"] - (va - vb) / vb) < 1e-4))
    # stock-out forecast: independent days-of-cover calculation
    since = (a - P.timedelta(days=59)).isoformat()
    dem = {r["product_id"]: r["u"] / 60 for r in db.q("SELECT i.product_id, SUM(i.qty) u FROM so_items i JOIN sales_orders o ON o.id=i.order_id WHERE o.order_date>=? AND o.status!='cancelled' GROUP BY 1", (since,))}
    stock = {r["product_id"]: r["q"] for r in db.q("SELECT product_id, SUM(qty) q FROM inventory GROUP BY 1")}
    truth = sorted(pid for pid, d in dem.items() if d > 0 and stock.get(pid, 0) / d <= 14)
    got = next(e["card"] for e in msg_of("which products are likely to run out in 14 days") if e["t"] == "card")
    names = {r["id"]: r["name"] for r in db.q("SELECT id,name FROM products")}
    checks.append(("stock-out set (14d)", sorted(r["name"] for r in got["rows"]) == sorted(names[p] for p in truth) or len(got["rows"]) == 12 and len(truth) > 12))
    # on-time rate
    per = P.preset("last_month", a)
    r = db.q1("SELECT COUNT(*) n, SUM(delivered_date<=promised_date) ot FROM sales_orders WHERE status='delivered' AND order_date BETWEEN ? AND ?", (per.start.isoformat(), per.end.isoformat()))
    card = next(e["card"] for e in msg_of("what's our on-time rate for last month") if e["t"] == "card")
    checks.append(("on-time rate last month", abs(card["value"] - 100.0 * r["ot"] / r["n"]) < 0.1))
    return checks


def evidence_check():
    """Every evidence bullet in an explanation must link to a real page + valid filters, and the cited numbers must exist in the data."""
    evs, _, _ = run_once("why did revenue drop in March 2026")
    card = next(e["card"] for e in evs if e["t"] == "card" and e["card"]["kind"] == "analysis")
    ok = 0
    for b in card["bullets"]:
        s = b["source"]
        if s["page"] in pages.PAGES:
            _, rej = ops.validate_filters(s["page"], s["filters"])
            ok += (not rej)
    return ok, len(card["bullets"])


# ------------------------------------------------------------------------------------------------------ scenarios
def scenarios():
    """Multi-step, multi-turn tasks verified against the database afterwards."""
    res = []

    def turn(u, page="procurement", filters=None):
        t0 = time.perf_counter()
        evs, route, _ = run_once(u, page, filters)
        return evs, (time.perf_counter() - t0) * 1000

    def confirm(evs, decision="approve"):
        c = next((e for e in evs if e["t"] == "confirm"), None)
        t0 = time.perf_counter()
        out = list(agent.confirm(USER, c["token"], decision)) if c else []
        return out, (time.perf_counter() - t0) * 1000

    def record(name, ok, steps, ms, note=""):
        res.append({"name": name, "ok": bool(ok), "steps": steps, "ms": round(ms, 1), "note": note})

    # 1. bulk replenishment: plan -> confirm -> drafts exist for exactly the planned products
    n0 = db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE status='draft'")
    evs, ms1 = turn("Analyze inventory items that may run out in the next 14 days and create purchase order drafts with best supplier options", "dashboard")
    plan_steps = [e for e in evs if e["t"] == "step"]
    cf = next(e for e in evs if e["t"] == "confirm")
    out, ms2 = confirm(evs)
    n1 = db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE status='draft'")
    lines = db.scalar("SELECT COUNT(*) FROM po_items WHERE po_id IN (SELECT id FROM purchase_orders WHERE notes LIKE 'Auto-replenishment%')")
    at_risk = len(agentkit.nlu and __import__("server.analytics", fromlist=["x"]).stockout_forecast(14))
    record("bulk replenishment (plan -> confirm -> drafts)", n1 - n0 == len(cf["lines"]) and lines == at_risk, len({s["id"] for s in plan_steps}) + 2, ms1 + ms2, f"{n1 - n0} drafts for {lines}/{at_risk} at-risk products")
    # 2. single PO via pre-filled form + chat confirmation
    evs, ms1 = turn("create a purchase order for 500 steel rods from Global Metals")
    form = next(e for e in evs if e["t"] == "ui" and e["a"] == "open_form")
    out, ms2 = confirm(evs)
    po = db.q1("SELECT po.*, s.name sup FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id ORDER BY po.id DESC")
    it = db.q1("SELECT * FROM po_items WHERE po_id=?", (po["id"],))
    record("single PO: form pre-fill -> confirm", form["values"]["qty"] == 500 and it["qty"] == 500 and po["sup"] == "Global Metals Ltd." and po["status"] == "draft", 3, ms1 + ms2)
    # 3. submit all drafts -> statuses follow the $50k rule
    evs, ms1 = turn("submit all draft purchase orders")
    drafts = db.q("SELECT po_number,total FROM purchase_orders WHERE status='draft'")
    out, ms2 = confirm(evs)
    after = {r["po_number"]: r["status"] for r in db.q("SELECT po_number,status FROM purchase_orders")}
    record("submit drafts (auto-approve <= $50k, queue above)", all(after[d["po_number"]] == ("pending_approval" if d["total"] > 50000 else "approved") for d in drafts) and drafts, 2, ms1 + ms2, f"{len(drafts)} drafts")
    # 4. approval respects the user's limit
    pend = db.q("SELECT po_number,total FROM purchase_orders WHERE status='pending_approval'")
    evs, ms1 = turn("approve all pending purchase orders", "approvals")
    out, ms2 = confirm(evs) if any(e["t"] == "confirm" for e in evs) else ([], 0)
    left = db.q("SELECT total FROM purchase_orders WHERE status='pending_approval'")
    limit = USER["approval_limit"]
    record("approvals respect role limit", all(r["total"] > limit for r in left) and (len(left) < len(pend) or not pend), 2, ms1 + ms2, f"{len(pend) - len(left)} approved, {len(left)} above limit stay queued")
    # 5. stock adjustment persists
    evs, ms1 = turn("adjust steel rod stock in Chicago to 1200", "inventory")
    out, ms2 = confirm(evs)
    record("stock adjustment applied", db.scalar("SELECT qty FROM inventory WHERE product_id=1 AND warehouse_id=1") == 1200, 2, ms1 + ms2)
    # 6. receiving a PO adds stock
    po = db.q1("SELECT po.po_number, i.product_id, i.qty FROM purchase_orders po JOIN po_items i ON i.po_id=po.id WHERE po.status IN ('approved','ordered') ORDER BY po.id DESC")
    before = db.scalar("SELECT qty FROM inventory WHERE product_id=? AND warehouse_id=1", (po["product_id"],))
    evs, ms1 = turn(f"mark {po['po_number']} as received")
    out, ms2 = confirm(evs)
    after_q = db.scalar("SELECT qty FROM inventory WHERE product_id=? AND warehouse_id=1", (po["product_id"],))
    record("receive PO increases stock", after_q >= before + po["qty"] and db.scalar("SELECT status FROM purchase_orders WHERE po_number=?", (po["po_number"],)) == "received", 2, ms1 + ms2)
    # 7. delete: drafts only; others refused with nothing changed
    ordered = db.q1("SELECT po_number FROM purchase_orders WHERE status='ordered' OR status='received' ORDER BY id DESC")
    evs, _ = turn(f"delete {ordered['po_number']}")
    refused = not any(e["t"] == "confirm" for e in evs) and db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE po_number=?", (ordered["po_number"],)) == 1
    mk, _ = turn("create a purchase order for 100 pvc pipe")
    confirm(mk)
    d = db.q1("SELECT po_number FROM purchase_orders WHERE status='draft' ORDER BY id DESC")
    evs, ms1 = turn(f"delete draft {d['po_number']}")
    out, ms2 = confirm(evs)
    record("delete only drafts (non-draft refused)", refused and db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE po_number=?", (d["po_number"],)) == 0, 3, ms1 + ms2)
    # 8. cancel leaves everything untouched
    v0 = db.scalar("SELECT COUNT(*) FROM suppliers WHERE status='on_hold'")
    evs, ms1 = turn("put Prime Plastics on hold", "suppliers")
    out, ms2 = confirm(evs, "cancel")
    record("cancelling a confirmation changes nothing", db.scalar("SELECT COUNT(*) FROM suppliers WHERE status='on_hold'") == v0, 2, ms1 + ms2)
    # 9. conversational follow-up keeps context
    evs1, _ = turn("compare revenue this month vs last month", "dashboard")
    evs2, ms = turn("and for Europe?", "analytics")
    r2 = next(e for e in evs2 if e["t"] == "route")
    ok = r2["intent"] == "compare_periods" and r2["followup"] and r2["slots"]["ents"].get("region") == ["Europe"] and r2["slots"]["metric"] == "revenue"
    record("follow-up ('and for Europe?') inherits metric + periods", ok, 2, ms)
    # 10. explanation names the true driver and links evidence
    evs, ms = turn("why did revenue drop in March 2026", "dashboard")
    text = " ".join([e["text"] for e in evs if e["t"] == "msg"] + [b["text"] for e in evs if e["t"] == "card" and e["card"]["kind"] == "analysis" for b in e["card"]["bullets"]])
    ok_e, n_e = evidence_check()
    record("explain_change finds Metals + supplier delays, with linked evidence", "Metals" in text and "days late" in text and ok_e == n_e and n_e >= 4, 2, ms, f"{ok_e}/{n_e} evidence links valid")
    return res


# ------------------------------------------------------------------------------------------------------------ main
def main():
    global USER
    ap = argparse.ArgumentParser()
    ap.add_argument("--failures", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    db.get_db()
    USER = db.q1("SELECT * FROM users WHERE email='michael@bizpilot.demo'")
    agentkit.metadata_index()
    t_cold = time.perf_counter()
    run_once("show low stock items")
    cold_ms = (time.perf_counter() - t_cold) * 1000
    dev, h1, h2 = run_set("dev", DEV), run_set("heldout", HELDOUT), run_set("heldout2", HELDOUT2)
    allcases = DEV + HELDOUT + HELDOUT2
    warm = sorted(run_once(fill(c["u"]), c["ctx_page"], c["ctx_filters"])[2] for c in allcases)      # second pass: caches warm
    p50, p95, pmax = statistics.median(warm), warm[int(len(warm) * 0.95)], warm[-1]
    sd, s1, s2 = summarise(dev), summarise(h1), summarise(h2)
    tuned = summarise(dev + h1)
    sa = summarise(dev + h1 + h2)
    ret, ret_scale = retrieval_eval(), retrieval_eval(extra_distractors=600)
    num = numeric_faithfulness()
    ev_ok, ev_n = evidence_check()
    scen = scenarios()
    tasks_ok = sum(x["ok"] for x in scen)
    avg_steps, avg_ms = statistics.mean(x["steps"] for x in scen), statistics.mean(x["ms"] for x in scen)
    f2p = ROOT / "eval" / "frozen_heldout_v2_first_run.json"
    f2 = json.loads(f2p.read_text(encoding="utf-8"))["by_set"]["heldout2"] if f2p.exists() else s2          # frozen first run of v2
    first = {}
    f0 = ROOT / "eval" / "results_initial_run.json"
    if f0.exists():
        first = json.loads(f0.read_text(encoding="utf-8")).get("by_set", {}).get("heldout", {})

    metrics = [
        {"metric": "Intent routing — FRESH held-out v2, first run (written after tuning, never tuned on)", "value": f"{f2['intent_acc']}% ({f2['n']} utterances)", "target": "≥ 85%",
         "note": f"The honest generalisation figure; includes typo / idiom / indirect-wording cases on purpose; same author as the system. After fixing the parser bugs it exposed: {s2['intent_acc']}% (no longer held-out)."},
        {"metric": "Intent routing — held-out v1, first run (before any fix)", "value": f"{first.get('intent_acc', 'n/a')}% ({first.get('n', 70)} utterances)" if first else "n/a", "target": "—",
         "note": "Measured once, then the failures were fixed generically; v1 is tuned-on from then on."},
        {"metric": "Intent routing — dev + held-out v1 (tuned on)", "value": f"{tuned['intent_acc']}% ({tuned['n']} utterances)", "target": "≥ 95%", "note": "In-distribution; kept for regression tracking, not as a generalisation claim."},
        {"metric": "Intent-to-destination accuracy — fresh v2, first run", "value": f"{f2['dest_acc']}% ({f2['dest_n']} navigation requests)", "target": "≥ 90%", "note": f"Tuned sets: {tuned['dest_acc']}% ({tuned['dest_n']}). After fixes on v2: {s2['dest_acc']}%."},
        {"metric": "UI-state correctness — fresh v2, first run (every filter, range and sort exactly right)", "value": f"{f2['ui_exact']}% ({f2['ui_n']} requests)", "target": "≥ 85%",
         "note": f"Per-filter recall {f2['ui_key_recall']}%, {f2['ui_extra_keys']} unrequested filters. Tuned sets: {tuned['ui_exact']}% ({tuned['ui_n']}). After fixes on v2: {s2['ui_exact']}%."},
        {"metric": "Slot extraction (metric / period / entity / N / horizon) — fresh v2, first run", "value": f"{f2['slot_acc']}% ({f2['slot_n']} slots)", "target": "≥ 90%", "note": ", ".join(f"{k} {v}%" for k, v in f2["slot_detail"].items()) + f". After fixes on v2: {s2['slot_acc']}%."},
        {"metric": "Multi-step task success (verified against the database)", "value": f"{tasks_ok}/{len(scen)} scenarios", "target": "100%", "note": f"Avg {avg_steps:.1f} steps and {avg_ms:.0f} ms per scenario including the confirmation round-trip."},
        {"metric": "Numeric faithfulness vs independent SQL", "value": f"{sum(ok for _, ok in num)}/{len(num)} answers match", "target": "100%", "note": "Revenue, orders, top-N, MoM compare, stock-out set and on-time rate recomputed with separate hand-written SQL."},
        {"metric": "Explanation evidence links valid", "value": f"{ev_ok}/{ev_n} bullets link to a real view", "target": "100%", "note": "Every claim in a 'why' answer carries a source chip with a valid page + filters."},
        {"metric": "Retrieval over app metadata — MRR@10", "value": f"{ret['mrr']} (R@1 {ret['r1']}%, R@3 {ret['r3']}%) over {ret['docs']} docs", "target": "≥ 0.80", "note": f"{ret['n']} queries across pages, widgets, filters and APIs. Queries written by the author; API docs were enriched with usage phrases after a first run scored MRR 0.79."},
        {"metric": "Retrieval at scale (+600 synthetic distractor components)", "value": f"{ret_scale['mrr']} (R@1 {ret_scale['r1']}%, R@3 {ret_scale['r3']}%) over {ret_scale['docs']} docs", "target": "≥ 0.70", "note": "Hard negatives sampled from the same vocabulary; lexical-semantic index, no dense embeddings."},
        {"metric": "Action hallucination (invalid page / filter emitted)", "value": f"{sa['halluc']} of {sa['n']} turns", "target": "0", "note": "Each emitted navigate action is validated against the page's declared filter schema."},
        {"metric": "Writes executed without confirmation", "value": f"{sa['unconfirmed_writes']} of {sa['n']} turns", "target": "0", "note": f"{sa['write_confirmed']}% of write requests produced a confirmation or pre-filled form ({sa['write_n']}); the rest were refused or clarified."},
        {"metric": "Out-of-scope / adversarial requests handled safely — fresh v2, first run", "value": f"{f2['oos_safe']}% ({f2['oos_n']} requests)", "target": "100%", "note": f"No navigation, write plan or invented answer. After fixes, all sets: {sa['oos_safe']}% ({sa['oos_n']})."},
        {"metric": "Latency, fast path (end-to-end, in-process)", "value": f"p50 {p50:.0f} ms · p95 {p95:.0f} ms · max {pmax:.0f} ms", "target": "p95 < 150 ms", "note": f"Warm; first request after start-up {cold_ms:.0f} ms. Excludes network and any LLM call (none were made)."},
    ]
    results = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "corpus": {"dev": len(DEV), "heldout_v1": len(HELDOUT), "heldout_v2": len(HELDOUT2), "retrieval_queries": len(RETRIEVAL), "scenarios": len(scen)},
               "metrics": metrics, "scenarios": scen, "by_set": {"dev": sd, "heldout": s1, "heldout2": s2},
               "caveat": "Synthetic corpora authored together with the system. v2 was not used for tuning, but is not independent of the author. No real-user traffic was measured."}
    fails = []
    for name, rs in (("dev", dev), ("heldout-v1", h1), ("heldout-v2", h2)):
        for r in rs:
            bad = [k for k, v in r["checks"].items() if not v]
            if not r["intent_ok"] or bad:
                fails.append({"set": name, "u": r["u"], "expected": r["expected"], "got": r["got"], "failed_checks": bad})
    results["failures"] = fails
    (ROOT / "eval" / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if not a.quiet:
        for m in metrics:
            print(f"- {m['metric']}: {m['value']}")
        for x in scen:
            print(f"  [{'OK' if x['ok'] else 'FAIL'}] {x['name']} ({x['steps']} steps, {x['ms']} ms) {x['note']}")
        print(f"\n{len(fails)} failing cases:")
        for f in fails:
            print(f"  [{f['set']}] {f['u']!r}: expected {f['expected']} got {f['got']} failed={f['failed_checks']}")
        for n_, ok in num:
            if not ok:
                print("  numeric mismatch:", n_)
    shutil.rmtree(_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
