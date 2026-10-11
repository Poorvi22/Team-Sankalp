"""Agent building blocks: event protocol, formatting, metadata index, router, confirmation store."""
import time
import uuid
from collections import defaultdict

from . import db, intents, nlu, ops, pages
from .retrieval import Index

# --------------------------------------------------------------------------------------------------------- formatting
def usd(x, short=False, signed=False):
    x = float(x or 0)
    a = abs(x)
    sign = "-" if x < 0 else ("+" if signed and x > 0 else "")
    if short or a >= 1e5:
        if a >= 1e6:
            return f"{sign}${a / 1e6:,.2f}M"
        if a >= 1e3:
            return f"{sign}${a / 1e3:,.1f}K"
    return f"{sign}${a:,.0f}"


def fmt_value(v, fmt):
    if v is None:
        return "—"
    return {"usd": lambda: usd(v), "int": lambda: f"{v:,.0f}", "pct": lambda: f"{v:.1f}%", "days": lambda: f"{v:,.1f} days", "dec1": lambda: f"{v:.1f}"}.get(fmt, lambda: f"{v:,.2f}")()


def signed_value(v, fmt):
    if fmt == "usd":
        return usd(v, signed=True)
    return f"{v:+,.0f}" if fmt == "int" else f"{v:+.1f}" + ("pp" if fmt == "pct" else "")


def pct_txt(x, signed=True):
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# ----------------------------------------------------------------------------------------------------- event builders
def msg(text):
    return {"t": "msg", "text": text}


def card(kind, **kw):
    return {"t": "card", "card": {"kind": kind, **kw}}


def nav(page, filters=None, new_tab=False, label=None, replace=True):
    return {"t": "ui", "a": "navigate", "page": page, "filters": filters or {}, "new_tab": new_tab, "replace": replace, "label": label}


def src(label, page, filters=None):
    return {"label": label, "page": page, "filters": filters or {}}


def chips(items):
    return {"t": "chips", "items": [{"label": i if isinstance(i, str) else i[0], "send": i if isinstance(i, str) else i[1]} for i in items]}


def step(sid, label, state="running", ms=None):
    return {"t": "step", "id": sid, "label": label, "state": state, "ms": ms}


def describe_filters(page, filters):
    """Human-readable 'Status = Low stock · Region = Europe' for a filter dict."""
    pg = pages.PAGES[page]
    opts = pages.options()
    opts["products"] = pages.products_options()
    parts = []
    for f in pg["filters"]:
        v = filters.get(f["key"])
        if v in (None, ""):
            continue
        label = v
        choices = f.get("options") or opts.get(f.get("options_from"), [])
        for o in choices:
            if o["value"] == v:
                label = o["label"]
        if f["type"] == "date_range":
            label = nlu.P.resolve(v, db.as_of()).label
        parts.append(f"{f['label']} = {label}")
    return " · ".join(parts)


# -------------------------------------------------------------------------------------------- metadata index (retrieval)
_index = {"ix": None, "v": None}
WIDGET_WORDS = {"line": "line chart trend over time graph", "bar": "bar chart graph", "donut": "donut pie chart share breakdown", "table": "table list grid rows", "kpi_row": "KPI cards summary numbers"}
API_WORDS = r"\b(api|endpoint|call|route|operation|tool|function|rest)\b"
FILTER_WORDS = r"\b(filter|limit|restrict|narrow|only|sort|order by|dropdown)\b"


def metadata_index() -> Index:
    if _index["ix"] is not None and _index["v"] == db.data_version():
        return _index["ix"]
    ix = Index()
    opts = pages.options()
    opts["products"] = []
    for pid, pg in pages.PAGES.items():
        ix.add(f"page:{pid}", f"{pg['title']} page. {pg['description']} {pg['keywords']}", "page", page=pid)
        for w in pg["widgets"]:
            ix.add(f"widget:{pid}.{w['id']}", f"{w['title']}. {w['description']} {WIDGET_WORDS.get(w['type'], '')} (widget on the {pg['title']} page)", "widget", page=pid, widget=w["id"])
        for f in pg["filters"]:
            choices = f.get("options") or opts.get(f.get("options_from"), [])
            ix.add(f"filter:{pid}.{f['key']}", f"{f['label']} filter. {f.get('description', '')} Values: {', '.join(o['label'] for o in choices[:10])}. On the {pg['title']} page",
                   "filter", page=pid, filter=f["key"])
    for o in ops.OPS.values():
        ix.add(f"op:{o['id']}", f"{o['description']} {' . '.join(ops.OP_EXAMPLES.get(o['id'], []))} {o['method']} {o['path']} {' '.join(o['params'])}", "op", op=o["id"], risk=o["risk"])
    for n, (iid, ex) in enumerate(intents.expand_examples()):
        ix.add(f"ex:{iid}:{n}", ex, "intent_ex", intent=iid)
    ix.build()
    _index.update(ix=ix, v=db.data_version())
    return ix


def find_components(text, k=3):
    """Pages / widgets / filters / APIs most relevant to a request. Kind-aware: API-sounding queries favour operations,
    UI-sounding ones favour components, and filter-sounding ones favour filters."""
    import re
    ix = metadata_index()
    t = text.lower()
    w = {"op": 1.25 if re.search(API_WORDS, t) else 0.85, "filter": 1.2 if re.search(FILTER_WORDS, t) else 1.0}
    hits = ix.search(text, k=k + 12, kinds={"page", "widget", "filter", "op"})
    ranked = sorted(((d, s * w.get(d["kind"], 1.0)) for d, s in hits), key=lambda x: -x[1])[:k]
    return [{"id": d["id"], "kind": d["kind"], "score": round(s, 3)} for d, s in ranked]


def retrieve_context(text, k=3):
    """Top application components for an utterance — shown in the trace and used as fallback grounding."""
    return find_components(text, k)


# ------------------------------------------------------------------------------------------------------------ router
FOLLOWUP = None


def classify(p: nlu.Parse, ctx: dict, session: dict):
    """-> dict(intent, confidence, candidates, sim). Retrieval over delexicalised exemplars + cue evidence."""
    ix = metadata_index()
    dtext = nlu.delex(p)
    sim = defaultdict(float)
    for doc, score in ix.search(dtext, k=14, kinds={"intent_ex"}):
        i = doc["meta"]["intent"]
        sim[i] = max(sim[i], score)
    b = intents.boosts(p, ctx or {})
    scores = {i: sim.get(i, 0.0) + b.get(i, 0.0) for i in intents.INTENTS}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (top_i, top), (_, second) = ranked[0], ranked[1]
    conf = max(0.0, min(0.99, 0.30 + 0.55 * (top - second) + 0.22 * min(top, 2.0)))
    return {"intent": top_i, "confidence": round(conf, 3), "score": round(top, 3), "margin": round(top - second, 3),
            "candidates": [{"intent": i, "score": round(s, 3)} for i, s in ranked[:3]], "delex": dtext}


ACCEPT_SCORE, ACCEPT_MARGIN = 0.75, 0.10


def is_confident(route) -> bool:
    return route["score"] >= ACCEPT_SCORE and route["margin"] >= ACCEPT_MARGIN


# -------------------------------------------------------------------------------------------- confirmation (HITL)
PENDING: dict = {}
TTL = 900


def new_confirmation(user, title, calls, risk="write", lines=None, changes=None, warnings=None, group=None, after=None, button=None):
    token = uuid.uuid4().hex[:12]
    PENDING[token] = {"user": user["email"], "title": title, "calls": calls, "risk": risk, "after": after or [], "created": time.time(), "group": group}
    for k in [k for k, v in PENDING.items() if time.time() - v["created"] > TTL]:
        PENDING.pop(k, None)
    return {"t": "confirm", "token": token, "title": title, "risk": risk, "lines": lines or [], "changes": changes or [], "warnings": warnings or [],
            "group": group, "button": button or ("Delete" if risk == "destructive" else "Confirm")}


# ----------------------------------------------------------------------------------------------------- run context
class Run:
    def __init__(self, user, ctx, message, p, route, session):
        self.user, self.ctx, self.message, self.p, self.route, self.session = user, ctx or {}, message, p, route, session
        self.tools, self.ui = [], []
        self.as_of = db.as_of()
        self.status = "ok"
        self.detail = {}

    @property
    def page(self):
        return self.ctx.get("page")

    def call(self, op_id, params=None):
        t = time.perf_counter()
        try:
            return ops.call(op_id, self.user, params or {})
        finally:
            self.tools.append({"tool": op_id, "ms": round((time.perf_counter() - t) * 1000, 1)})

    def timed(self, name, fn, *a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            self.tools.append({"tool": name, "ms": round((time.perf_counter() - t) * 1000, 1)})
