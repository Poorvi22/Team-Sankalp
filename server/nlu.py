"""Natural-language understanding: turns an utterance into typed slots (periods, metric, entities, numbers, ids, statuses,
sort). Entity linking is grounded in the live database, so the agent can only ever refer to things that exist."""
import re
from dataclasses import dataclass, field

from . import db, periods as P
from .retrieval import stem, stems, words

CORP = {"ltd", "inc", "co", "corp", "group", "intl", "partners", "exports", "llc", "the"}
GENERIC_PRODUCT_TOKENS = {"sheet", "kit", "roll", "set", "unit", "pack", "module", "block", "fluid", "fitting", "pipe", "wire", "film", "coil"}

CATEGORY_ALIASES = {
    "Metals": ["metal", "metals", "metallic"], "Plastics & Polymers": ["plastic", "plastics", "polymer", "polymers"],
    "Electrical": ["electrical", "electronics", "electronic", "electric"], "Packaging": ["packaging", "package materials"],
    "Machinery Parts": ["machinery", "machine parts", "spare parts", "mechanical parts", "machinery parts"], "Chemicals": ["chemical", "chemicals"],
}
REGION_ALIASES = {
    "North America": [r"north america", r"\bn\.? ?america\b", r"\busa\b", r"united states", r"\bus market\b", r"\bnam\b"],
    "Europe": [r"europe", r"\beu\b", r"european", r"\bemea\b"],
    "APAC": [r"apac", r"asia[- ]pacific", r"\basia\b", r"asian"],
    "LATAM": [r"latam", r"latin america", r"south america"],
}
METRIC_PATTERNS = [   # most specific first
    ("cancel_rate", r"cancel+ation rate|cancel+ed rate|rate of cancel|(?:percent(?:age)?|share|proportion|%) (?:of )?(?:the )?orders (?:\w+ ){0,2}cancel|percent(?:age)? (?:of )?(?:orders )?cancel"),
    ("cancelled_orders", r"cancel+ed orders?|cancel+ations?|order cancel"),
    ("on_time_rate", r"on[- ]?time|fulfil+ment rate|delivery performance|order fulfil+ment|delivered on time|otd\b"),
    ("delayed_shipments", r"delay(?:ed)? shipments?|late shipments?|late deliver|shipment delays|delayed deliver"),
    ("avg_order_value", r"avg order|average order|order value|\baov\b|basket size"),
    ("po_spend", r"spend (?:on|for|with) (?:procurement|purchas\w*|suppliers?|vendors?)|(?:procurement|purchasing) (?:spend|spending|cost|budget)|procurement spend|purchas(?:e|ing) spend|po spend|spend (?:on|with) suppliers|supplier spend|purchase value|procurement cost"),
    ("inventory_value", r"inventory value|stock value|value of (?:the )?(?:inventory|stock)|inventory worth|stock worth"),
    ("active_suppliers", r"active suppliers?|number of suppliers|how many suppliers|supplier count"),
    ("receivables", r"receivables?|outstanding invoices?|unpaid invoices?|money owed|overdue invoices?|accounts receivable"),
    ("profit", r"\bprofit|margin|earnings|bottom line"),
    ("expenses", r"expenses?|\bcosts?\b|spending|\bopex\b"),
    ("units", r"units? sold|quantity sold|volume sold|\bunits\b|items sold|pieces sold"),
    ("orders", r"(?<!purchase )(?<!draft )(?<!supplier )(?<!vendor )\borders?\b|order volume|order count|number of orders"),
    ("revenue", r"revenue|sales|turnover|income|takings|\bsold\b|top ?line"),
]
STATUS_PHRASES = [   # regex -> list of (domain page, status value)
    (r"out[- ]of[- ]stock|stock[- ]?outs?|sold out|zero stock|no stock|depleted", [("inventory", "out_of_stock")]),
    (r"low[- ]stock|running low|low inventory|below (?:the )?reorder|need(?:s)? restock|short on stock|low on stock|\blow\b.{0,25}\b(?:stock|inventory)\b|\b(?:stock|inventory)\b.{0,25}\blow\b", [("inventory", "low_stock")]),
    (r"\bin[- ]stock\b|well stocked|healthy stock", [("inventory", "in_stock")]),
    (r"pending approvals?|awaiting (?:my |your |our )?approval|waiting (?:for )?(?:my |your |our )?approval|needs? (?:my |your |our )?approval|to be approved|pending review|waiting on (?:me|my approval)", [("procurement", "pending_approval"), ("approvals", "pending")]),
    (r"\bdrafts?\b", [("procurement", "draft")]),
    (r"\bapproved\b", [("procurement", "approved"), ("approvals", "approved")]),
    (r"\brejected\b", [("approvals", "rejected")]),
    (r"\breceived\b", [("procurement", "received")]),
    (r"\bordered\b", [("procurement", "ordered")]),
    (r"cancel+ed|cancel+ations?", [("sales", "cancelled"), ("procurement", "cancelled")]),
    (r"\bdelivered\b", [("sales", "delivered"), ("logistics", "delivered")]),
    (r"\bshipped\b", [("sales", "shipped")]),
    (r"\bprocessing\b", [("sales", "processing")]),
    (r"\bpending\b", [("sales", "pending"), ("approvals", "pending")]),
    (r"\b(?:delayed|late|overdue)\b|running late|past (?:the )?eta", [("logistics", "delayed")]),
    (r"in[- ]transit|on the way|on its way", [("logistics", "in_transit")]),
    (r"on[- ]hold|suspended", [("suppliers", "on_hold")]),
    (r"inactive|dormant", [("suppliers", "inactive")]),
    (r"\bactive\b", [("suppliers", "active")]),
]
NOUN_PAGE = [   # regex -> page. Order = priority
    (r"purchase orders?|\bpos?\b|procurement|purchasing|\brfq\b|replenish", "procurement"),
    (r"approvals?\b|\bapprove\b|sign[- ]?off", "approvals"),
    (r"shipments?|shipping|carriers?|freight|logistics|tracking|in[- ]transit|deliver(?:y|ies)\b", "logistics"),
    (r"suppliers?|vendors?|sourcing", "suppliers"),
    (r"inventory|\bstock\b|warehouses?|reorder|\bskus?\b|low stock|out of stock|stock levels?", "inventory"),
    (r"orders?\b|customers?|\bsales\b|clients?", "sales"),
    (r"finance|financial|expenses?|receivables?|invoices?|cash flow|profit|margin|\bcosts?\b", "finance"),
    (r"analytics|analysis|analy[sz]e|trend|compare|explore", "analytics"),
    (r"agent activity|audit|activity log|assistant history|benchmark|evaluation|scorecard", "agent_activity"),
    (r"dashboard|overview|home\b|executive|summary page|main page|landing", "dashboard"),
]
SORT_ALIASES = {
    "inventory": [(r"days? (?:left|of cover)|running out first|soonest|most urgent|runs? out first", "days_left:asc"), (r"lowest stock|least stock|smallest stock|fewest", "qty:asc"),
                  (r"highest stock|most stock|largest stock", "qty:desc"), (r"stock value|highest value|most valuable", "value:desc"), (r"alphabetical|by name|a to z", "name:asc")],
    "suppliers": [(r"best rated|highest rated|top rated|by rating|rating (?:first|order)", "rating:desc"), (r"by on[- ]time|on[- ]time (?:first|performance|order)|most reliable", "on_time_pct:desc"),
                  (r"shortest lead|fastest|by lead time", "lead_time_days:asc"), (r"most spend|highest spend|biggest spend|by spend", "spend:desc"), (r"alphabetical|by name", "name:asc")],
    "procurement": [(r"newest|latest|most recent|recent", "created_at:desc"), (r"oldest|earliest", "created_at:asc"), (r"largest|biggest|highest|most expensive|high to low|by amount|by value|by total", "total:desc"),
                    (r"smallest|lowest|cheapest|low to high", "total:asc")],
    "sales": [(r"newest|latest|most recent|recent", "order_date:desc"), (r"oldest|earliest", "order_date:asc"), (r"largest|biggest|highest|most valuable|high to low", "total:desc"),
              (r"smallest|lowest|cheapest|low to high", "total:asc")],
    "logistics": [(r"most delayed|longest delay|worst delay|biggest delay", "delay_days:desc"), (r"newest|latest|most recent|recent", "shipped_at:desc")],
}
PAGE_FILTER_ENTITIES = {   # which linked entity types become which filter on each page
    "inventory": {"category": "category", "warehouse": "warehouse"},
    "suppliers": {"category": "category", "country": "country"},
    "procurement": {"supplier": "supplier"},
    "sales": {"region": "region", "category": "category"},
    "logistics": {"region": "region", "carrier": "carrier"},
    "finance": {}, "dashboard": {"warehouse": "warehouse"}, "analytics": {"category": "category", "region": "region"}, "approvals": {}, "agent_activity": {},
}


@dataclass
class Ent:
    type: str
    id: object
    name: str
    score: float = 1.0
    pos: int = 0


@dataclass
class Parse:
    text: str
    t: str                                   # lowercase, normalised text
    periods: list = field(default_factory=list)
    metric: str | None = None
    metric_pos: int = 10_000
    ents: dict = field(default_factory=dict)  # type -> [Ent]
    product_group: tuple | None = None        # (term, [product ids]) when the user named only part of a product name
    po_refs: list = field(default_factory=list)
    so_refs: list = field(default_factory=list)
    top_n: int | None = None
    horizon_days: int | None = None
    numbers: list = field(default_factory=list)   # (value, start, end)
    money: tuple | None = None                    # (op, amount)
    statuses: list = field(default_factory=list)  # (page, value)
    sort_hint: dict = field(default_factory=dict)  # page -> sort string
    pages_mentioned: list = field(default_factory=list)
    new_tab: bool = False
    rating_min: float | None = None
    group_by: str | None = None
    entity_noun: str | None = None

    def rx(self, pattern: str) -> bool:
        return re.search(pattern, self.t) is not None

    def first(self, etype):
        v = self.ents.get(etype)
        return v[0] if v else None


# ------------------------------------------------------------------------------------------------ lexicon (DB-grounded)
_lex_cache = {"v": None, "lex": None}


def lexicon():
    if _lex_cache["v"] == db.data_version() and _lex_cache["lex"]:
        return _lex_cache["lex"]
    prods = [{"id": r["id"], "name": r["name"], "sku": r["sku"].lower(), "toks": set(stems(r["name"]))} for r in db.q("SELECT id,name,sku FROM products")]
    sups = []
    for r in db.q("SELECT id,name FROM suppliers"):
        toks = {t for t in stems(r["name"]) if t not in CORP}
        sups.append({"id": r["id"], "name": r["name"], "toks": toks})
    custs = [{"id": r["id"], "name": r["name"], "toks": set(stems(r["name"]))} for r in db.q("SELECT id,name FROM customers")]
    whs = [{"id": r["id"], "name": r["name"], "city": r["city"].lower()} for r in db.q("SELECT id,name,city FROM warehouses")]
    carriers = [r["carrier"] for r in db.q("SELECT DISTINCT carrier FROM shipments")]
    countries = [r["country"] for r in db.q("SELECT DISTINCT country FROM suppliers")]
    lex = {"products": prods, "suppliers": sups, "customers": custs, "warehouses": whs, "carriers": carriers, "countries": countries,
           "product_tokens": {t for p in prods for t in p["toks"]}}
    _lex_cache.update(v=db.data_version(), lex=lex)
    return lex


_NUM_RX = re.compile(r"(?<![\w.-])\$?(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\b", re.I)
_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20}


def parse(text: str) -> Parse:
    as_of = db.as_of()
    t = re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip()
    p = Parse(text=text, t=t)
    lex = lexicon()
    ws = stems(text)
    wset = set(ws)

    # ---- time
    p.periods = P.parse_periods(t, as_of)
    masked = t
    for per in p.periods:           # blank out time phrases so their digits are not mistaken for quantities
        masked = masked[:per.span[0]] + " " * (per.span[1] - per.span[0]) + masked[per.span[1]:]

    # ---- ids
    p.po_refs = [m.upper() for m in re.findall(r"\bpo-\d{4}-\d{3,5}\b", t)]
    p.so_refs = [m.upper() for m in re.findall(r"\bso-\d{5,7}\b", t)]
    masked = re.sub(r"\b(?:po|so)-[\d-]+\b", " ", masked)

    # ---- metric (earliest-position specific match wins ties by pattern order)
    for mid, pat in METRIC_PATTERNS:
        m = re.search(pat, t)
        if m:
            p.metric, p.metric_pos = mid, m.start()
            break

    # ---- numbers
    for m in _NUM_RX.finditer(masked):
        v = float(m.group(1).replace(",", ""))
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((m.group(2) or "").lower(), 1)
        p.numbers.append((v * mult, m.start(), m.end()))
    for w, n in _WORD_NUMS.items():
        for m in re.finditer(rf"\b(top|best|worst|bottom|next|first|last)\s+{w}\b", t):
            p.numbers.append((float(n), m.start(), m.end()))
    m = re.search(r"\b(?:top|best|worst|bottom|first|biggest|largest|highest|lowest|leading)\s+(\d+|" + "|".join(_WORD_NUMS) + r")\b", t) or \
        re.search(r"\b(\d+|" + "|".join(_WORD_NUMS) + r")\s+(?:best|top|worst|biggest|largest|highest|lowest)\b", t)
    if m:
        g = m.group(1)
        p.top_n = int(g) if g.isdigit() else _WORD_NUMS[g]
    m = re.search(r"\b(?:next|within|in the next|in|over the next|coming|upcoming)\s+(\d+|" + "|".join(_WORD_NUMS) + r")\s*(day|week|month)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUMS[m.group(1)]
        p.horizon_days = n * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    elif re.search(r"\bnext week\b|\bthis week\b.*run out|\bwithin a week\b", t):
        p.horizon_days = 7
    elif re.search(r"\bnext month\b", t):
        p.horizon_days = 30
    elif re.search(r"\btomorrow\b|\bnext (?:few )?days\b", t):
        p.horizon_days = 3 if "tomorrow" not in t else 1
    m = re.search(r"(under|below|less than|up to|at most|max(?:imum)?|<=?|over|above|more than|greater than|at least|>=?)\s*\$?(\d[\d,.]*)\s*(k|m|million|thousand)?", masked)
    if m and not re.search(r"(rating|star)", masked[m.end():m.end() + 8]):
        amt = float(m.group(2).replace(",", "")) * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((m.group(3) or "").lower(), 1)
        p.money = ("lt" if m.group(1) in ("under", "below", "less than", "up to", "at most", "max", "maximum", "<", "<=") else "gt", amt)
    m = re.search(r"(?:rated?|rating|stars?)\s*(?:of\s*)?(?:above|over|at least|>=?|more than)?\s*(\d(?:\.\d)?)\+?|(\d(?:\.\d)?)\+?\s*(?:stars?|rating)", t)
    if m and re.search(r"rating|rated|star", t):
        v = float(m.group(1) or m.group(2))
        if 1 <= v <= 5:
            p.rating_min = v

    # ---- entities: categories / regions / warehouses / carriers / countries
    def add(etype, id_, name, score=1.0, pos=0):
        p.ents.setdefault(etype, []).append(Ent(etype, id_, name, score, pos))
    for cat, aliases in CATEGORY_ALIASES.items():
        for a in aliases:
            m = re.search(rf"\b{re.escape(a)}\b", t)
            if m:
                add("category", cat, cat, 1.0, m.start())
                break
    for reg, pats in REGION_ALIASES.items():
        for pat in pats:
            m = re.search(pat, t)
            if m:
                add("region", reg, reg, 1.0, m.start())
                break
    for w in lex["warehouses"]:
        m = re.search(rf"\b{re.escape(w['city'])}\b", t)
        if m:
            add("warehouse", str(w["id"]), w["name"], 1.0, m.start())
    for c in lex["carriers"]:
        m = re.search(rf"\b{re.escape(c.lower())}\b", t) or (re.search(rf"\b{re.escape(c.lower().split()[0])}\b", t) if " " in c else None)
        if m:
            add("carrier", c, c, 1.0, m.start())
    for c in lex["countries"]:
        m = re.search(rf"\b{re.escape(c.lower())}\b", t)
        if m:
            add("country", c, c, 1.0, m.start())

    # ---- products: full-name match first, otherwise a "group" by distinctive token ("steel" -> all steel items)
    used = set()
    full = []
    for pr in lex["products"]:
        if pr["toks"] <= wset or pr["sku"] in t:
            pos = min((ws.index(x) for x in pr["toks"] if x in ws), default=0) if pr["toks"] <= wset else t.find(pr["sku"])
            full.append((len(pr["toks"]) + (1 if pr["sku"] in t else 0), pos, pr))
    full.sort(key=lambda x: (-x[0], x[1]))
    covered = set()
    for _, pos, pr in full:
        # drop products whose tokens are fully contained in a longer matched product ("Steel Rod" inside "Stainless Steel Rod")
        if any(pr["toks"] < q["toks"] for _, _, q in full):
            continue
        add("product", pr["id"], pr["name"], 1.0, pos)
        covered |= pr["toks"]
    if not p.ents.get("product"):
        cat_tokens = {stem(a) for aliases in CATEGORY_ALIASES.values() for a in aliases for a in a.split()}
        cands = [tok for tok in ws if tok in lex["product_tokens"] and tok not in GENERIC_PRODUCT_TOKENS and tok not in cat_tokens]
        if cands:
            tok = cands[0]
            ids = [pr["id"] for pr in lex["products"] if tok in pr["toks"]]
            p.product_group = (tok, ids)

    # ---- suppliers / customers (all distinctive tokens present)
    for s in lex["suppliers"]:
        if s["toks"] and s["toks"] <= wset and (len(s["toks"]) > 1 or next(iter(s["toks"])) not in lex["product_tokens"]):
            add("supplier", s["id"], s["name"], 1.0, min(ws.index(x) for x in s["toks"]))
    for c in lex["customers"]:
        if c["toks"] <= wset:
            add("customer", c["id"], c["name"], 1.0, min(ws.index(x) for x in c["toks"]))
    for k in p.ents:
        p.ents[k].sort(key=lambda e: e.pos)

    # ---- statuses
    for pat, doms in STATUS_PHRASES:
        if re.search(pat, t):
            p.statuses.extend(doms)
    # "active suppliers" is a metric, not a filter; "approved orders" => procurement
    # ---- sort hints per page
    for pg, rules in SORT_ALIASES.items():
        for pat, val in rules:
            if re.search(pat, t):
                p.sort_hint[pg] = val
                break

    # ---- page mentions (for navigation), in order of appearance
    seen = []
    for pat, pg in NOUN_PAGE:
        for m in re.finditer(pat, t):
            seen.append((m.start(), pg))
    seen.sort()
    for _, pg in seen:
        if pg not in p.pages_mentioned:
            p.pages_mentioned.append(pg)
    p.new_tab = bool(re.search(r"new tabs?|separate tabs?|another tab|in tabs?\b|own tabs?|side by side|each in (?:a )?tab", t))
    # group-by dimension
    for dim, pat in (("category", r"by (?:product )?categor"), ("region", r"by (?:region|market|territory|geograph)"), ("product", r"by product|per product|product[- ]wise"),
                     ("customer", r"by customer|per customer"), ("month", r"by month|monthly|month[- ]wise|per month|month over month|\bmom\b")):
        if re.search(pat, t):
            p.group_by = dim
            break
    # entity noun for top-N style questions
    for noun, pat in (("product", r"\b(products?|items?|skus?|materials?)\b"), ("customer", r"\b(customers?|clients?|accounts?|buyers?)\b"),
                      ("supplier", r"\b(suppliers?|vendors?)\b"), ("region", r"\b(regions?|markets?|territor(?:y|ies)|countries)\b"),
                      ("category", r"\b(categor(?:y|ies))\b"), ("warehouse", r"\b(warehouses?|locations?)\b")):
        m = re.search(pat, t)
        if m:
            p.entity_noun = p.entity_noun or noun
    return p


def delex(p: Parse) -> str:
    """Replace slot values with placeholder tokens so retrieval compares *shape*, not entity names."""
    s = p.t
    for per in sorted(p.periods, key=lambda x: -x.span[0]):
        s = s[:per.span[0]] + " periodtok " + s[per.span[1]:]
    s = re.sub(r"\bpo-\d{4}-\d{3,5}\b", " poreftok ", s)
    s = re.sub(r"\bso-\d{5,7}\b", " soreftok ", s)
    s = re.sub(r"\b[a-z]{2}-\d{3}\b", " entproduct ", s)
    for e in p.ents.get("product", []):
        pat = r"\W+".join(re.escape(w) + r"s?" for w in words(e.name))
        s = re.sub(rf"\b{pat}\b", " entproduct ", s)
    if p.product_group:
        s = re.sub(rf"\b{re.escape(p.product_group[0])}\w*", " entproduct ", s)
    for kind, tok in (("supplier", "entsupplier"), ("customer", "entcustomer")):
        for e in p.ents.get(kind, []):
            ws_ = [w for w in words(e.name) if w not in CORP]
            pat = r"\W+".join(re.escape(w) + r"\w*" for w in ws_)
            s = re.sub(rf"\b{pat}\b(?:\s+(?:ltd|inc|co|corp|group|llc)\b\.?)?", f" {tok} ", s)
    for cat, aliases in CATEGORY_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            s = re.sub(rf"\b{re.escape(a)}\b", " entcategory ", s)
    for reg, pats in REGION_ALIASES.items():
        for pat in pats:
            s = re.sub(pat, " entregion ", s)
    for w in lexicon()["warehouses"]:
        s = re.sub(rf"\b{re.escape(w['city'])}\b(?: (?:dc|hub|warehouse))?", " entwarehouse ", s)
    for c in lexicon()["countries"]:
        s = re.sub(rf"\b{re.escape(c.lower())}\b", " entcountry ", s)
    s = re.sub(r"(under|below|over|above|less than|more than|up to|at most|at least)\s*\$?\d[\d,.]*\s*(?:k|m)?\b", r"\1 moneytok", s)
    s = re.sub(r"\$\s?\d[\d,.]*\s*(?:k|m)?\b", " moneytok ", s)
    s = re.sub(r"(?<![\w])\d[\d,]*(?:\.\d+)?\s*(?:k|m)?\b", " numtok ", s)
    return re.sub(r"\s+", " ", s).strip()
