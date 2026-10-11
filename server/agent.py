"""Agent controller: parse -> route -> (LLM tier if unsure) -> handler -> audit. Streams events to the UI."""
import json
import re
import time
from datetime import datetime, timezone

from . import db, intents, llm, nlu, ops, pages
from . import handlers_write  # noqa: F401  (registers write handlers)
from .agentkit import (PENDING, Run, chips, classify, is_confident, metadata_index, msg, nav, plural, retrieve_context, usd)
from .handlers_read import HANDLERS

SESSIONS: dict = {}
FOLLOW_RX = re.compile(r"^(and|what about|how about|same|now|also|then|what if|and for|for|in)\b")
CARRY_INTENTS = {"kpi_query", "trend", "compare_periods", "explain_change", "top_n"}


def session_for(user):
    return SESSIONS.setdefault(user["email"], {"last": None})


def _ms(t0):
    return round((time.perf_counter() - t0) * 1000, 1)


def _apply_followup(p, last):
    """'and for Europe?' / 'what about last quarter?' — inherit the missing slots from the previous analytic turn."""
    if not p.metric and last.get("metric"):
        p.metric = last["metric"]
    if not p.periods and last.get("periods"):
        p.periods = last["periods"]
    for k, v in (last.get("ents") or {}).items():
        if not p.ents.get(k):
            p.ents[k] = v
    if last.get("entity_noun") and not p.entity_noun:
        p.entity_noun = last["entity_noun"]


def audit(user, message, tier, intent, conf, page, tools, ui, status, route_ms, total_ms, detail):
    db.get_db().execute(
        "INSERT INTO agent_audit(ts,user_email,utterance,tier,intent,confidence,page,tools,actions,status,route_ms,total_ms,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), user["email"], message[:500], tier, intent, conf, page, json.dumps(tools), json.dumps(ui)[:2000], status, route_ms, total_ms, json.dumps(detail)[:2000]))
    db.get_db().commit()


def run_turn(user, message, ctx):
    """Generator of events (dicts). Always ends with a {'t': 'done'} event."""
    t0 = time.perf_counter()
    ctx = ctx or {}
    message = (message or "").strip()[:500]
    if not message:
        yield msg("Say something like “show low stock items”.")
        yield {"t": "done", "meta": {"intent": None, "tier": "fast", "total_ms": 0}}
        return
    sess = session_for(user)
    p = nlu.parse(message)
    route = classify(p, ctx, sess)
    tier = "fast"
    last = sess.get("last")
    followup = False
    if last and not is_confident(route) and (FOLLOW_RX.match(p.t) or p.periods or p.ents.get("region") or p.ents.get("category")) and last["intent"] in CARRY_INTENTS:
        _apply_followup(p, last)
        route = dict(route, intent=last["intent"], confidence=0.8, score=1.0, margin=0.5)
        followup = True
    if not is_confident(route) and not followup:
        rewritten = None
        if llm.enabled():
            t_llm = time.perf_counter()
            res = llm.route(message, ctx.get("page"), db.as_of().isoformat(), intents.INTENTS)
            if res:
                p2 = nlu.parse(res["rewrite"])
                r2 = classify(p2, ctx, sess)
                intent = res["intent"]
                p, route, tier, rewritten = p2, dict(r2, intent=intent, confidence=0.85), "llm", res["rewrite"]
                route["llm_ms"] = _ms(t_llm)
        if tier != "llm":
            tier = "fallback"
    route_ms = _ms(t0)
    run = Run(user, ctx, message, p, route, sess)
    slots = {"metric": p.metric, "periods": [{"start": x.start.isoformat(), "end": x.end.isoformat()} for x in p.periods],
             "ents": {k: [e.name for e in v] for k, v in p.ents.items()}, "product_group": p.product_group[0] if p.product_group else None,
             "top_n": p.top_n, "horizon_days": p.horizon_days}
    yield {"t": "route", "intent": route["intent"] if tier != "fallback" else None, "confidence": route["confidence"], "tier": tier, "route_ms": route_ms, "followup": followup,
           "candidates": route["candidates"], "retrieved": retrieve_context(message), "delex": route.get("delex"), "slots": slots}
    status, intent = "ok", route["intent"]
    try:
        if tier == "fallback":
            run.status = "clarify"
            yield from _fallback(run, route)
        else:
            for ev in HANDLERS[intent](run):
                if ev.get("t") == "ui":
                    run.ui.append({k: ev.get(k) for k in ("a", "page", "form")})
                yield ev
            status = run.status
            if intent in CARRY_INTENTS and status == "ok":
                sess["last"] = {"intent": intent, "metric": p.metric, "periods": p.periods, "ents": {k: v for k, v in p.ents.items() if k in ("region", "category")}, "entity_noun": p.entity_noun}
            elif intent not in CARRY_INTENTS:
                sess["last"] = sess.get("last") if intent in ("chitchat", "help") else None
    except ops.OpError as e:
        status = "error"
        yield msg(f"I couldn't do that: {e}")
    except Exception as e:  # never leak a stack trace into the chat; keep it in the audit log
        status = "error"
        yield msg("Something went wrong while handling that request. Nothing was changed.")
        run.detail["exception"] = f"{type(e).__name__}: {e}"
    total = _ms(t0)
    if tier == "fallback":
        status = "clarify"
    run.detail.update(route=route.get("candidates"), followup=followup)
    if not ctx.get("_noaudit"):          # offline evaluation runs must not pollute the audit log
        audit(user, message, tier, intent if tier != "fallback" else None, route["confidence"], ctx.get("page"), run.tools, run.ui, status, route_ms, total, run.detail)
    yield {"t": "done", "meta": {"intent": intent if tier != "fallback" else None, "confidence": route["confidence"], "tier": tier, "route_ms": route_ms, "total_ms": total,
                                 "tools": run.tools, "status": status}}


def _fallback(run, route):
    sugg = []
    for c in route["candidates"][:3]:
        exs = [e for e in intents.INTENTS[c["intent"]]["examples"] if "{" not in e]
        if exs:
            sugg.append(exs[0][0].upper() + exs[0][1:])
    top_page = metadata_index().search(run.message, k=1, kinds={"page"})
    page_hint = ""
    if top_page and top_page[0][1] >= 0.15:
        pid = top_page[0][0]["meta"]["page"]
        page_hint = f" If you just want to look around, I can open **{pages.PAGES[pid]['title']}**."
        sugg.append(f"Open {pages.PAGES[pid]['title']}")
    yield msg("I'm not sure what you'd like me to do — I'd rather ask than guess." + page_hint + "\nDid you mean one of these?")
    yield chips(sugg or ["What can you do?"])


# -------------------------------------------------------------------------------------------------- confirmations
def confirm(user, token, decision):
    t0 = time.perf_counter()
    ent = PENDING.pop(token, None)
    if not ent or ent["user"] != user["email"]:
        yield msg("That confirmation has expired or was already used. Ask me again and I'll prepare it afresh.")
        yield {"t": "done", "meta": {"status": "expired", "total_ms": 0}}
        return
    group = ent["group"] or "action"
    if decision != "approve":
        yield msg("Okay — cancelled. Nothing was changed.")
        audit(user, f"[confirm:{group}] cancelled", "confirm", f"confirm:{group}", 1.0, None, [], [], "cancelled", 0, _ms(t0), {"title": ent["title"]})
        yield {"t": "done", "meta": {"status": "cancelled", "total_ms": _ms(t0)}}
        return
    results, errors, tools = [], [], []
    for op_id, params in ent["calls"]:
        t1 = time.perf_counter()
        try:
            results.append((op_id, ops.call(op_id, user, params)))
        except ops.OpError as e:
            errors.append(f"{params.get('po') or params.get('supplier') or params.get('product') or params.get('order') or op_id}: {e}")
        tools.append({"tool": op_id, "ms": _ms(t1)})
    lines = []
    for op_id, r in results:
        if op_id == "po.create":
            lines.append(f"Created **{r['po']['po_number']}** — {r['supplier']}, {usd(r['po']['total'])} (draft)" + (f" ⚠ {'; '.join(r['warnings'])}" if r["warnings"] else ""))
        elif op_id in ("po.submit", "po.approve", "po.reject"):
            lines.append(f"**{r['po_number']}** → {r['status'].replace('_', ' ')}")
        elif op_id == "po.receive":
            lines.append(f"**{r['po_number']}** received into {r['warehouse']} — stock updated")
        elif op_id == "po.delete":
            lines.append(f"Deleted **{r['po_number']}**")
        elif op_id == "inventory.update":
            lines.append(f"**{r['product']}** updated — {r['total_qty']:,} units total, reorder level {r['total_reorder_level']:,}")
        elif op_id == "order.update_status":
            lines.append(f"**{r['so_number']}**: {r['from']} → {r['status']}")
        elif op_id == "supplier.update":
            lines.append(f"**{r['supplier']}**: {r['from'].replace('_', ' ')} → {r['status'].replace('_', ' ')}")
        else:
            lines.append(f"{op_id} done")
    ok = bool(results)
    head = (f"✅ Done — {plural(len(results), 'change')} applied." if not errors else f"Applied {len(results)} of {len(ent['calls'])} changes.") if ok else "❌ Nothing was changed."
    yield msg(head + ("\n" + "\n".join("- " + l for l in lines) if lines else "") + ("\n**Could not apply:**\n" + "\n".join("- " + e for e in errors) if errors else ""))
    for ev in ent["after"]:
        yield ev
    yield {"t": "ui", "a": "refresh"}
    status = "ok" if ok and not errors else "partial" if ok else "error"
    audit(user, f"[confirm:{group}] {ent['title']}", "confirm", f"confirm:{group}", 1.0, None, tools, [], status, 0, _ms(t0), {"results": len(results), "errors": errors})
    yield {"t": "done", "meta": {"status": status, "total_ms": _ms(t0), "tools": tools}}


# ---------------------------------------------------------------------------------------------------------- eval store
def last_eval():
    f = db.ROOT / "eval" / "results.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:
        return None
