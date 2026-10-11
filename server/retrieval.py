"""In-process hybrid retrieval over application metadata (pages, widgets, filters, APIs, intent exemplars).

Features per text: canonical word stems (domain synonyms folded together), word bigrams and character 3-grams (typo
tolerance), weighted TF-IDF, cosine similarity through an inverted index. Dependency-free and ~1 ms per query for the
few hundred documents of this app. The `Index` interface is what a dense backend (FAISS / pgvector over sentence
embeddings) would replace at larger scale; `search()` returns the same shape either way.
"""
import math
import re
from collections import Counter, defaultdict

STOP = set("""a an the of to for in on at by with and or is are was were be been am do does did can could would should will shall
me my we our us you your i it its this that these those there here please kindly just so as from into about over than then
also any some all each what which who whom whose how when where why if not no up out""".split())
KEEP = {"up", "out", "all", "over", "no", "not", "than", "where", "what", "why", "how", "which"}   # carry intent signal

_CANON_SRC = {
    # domain synonyms -> canonical word
    "vendor": "supplier", "vendors": "supplier", "sourcing": "supplier", "supplies": "supplier",
    "po": "purchase order", "pos": "purchase order", "purchasing": "purchase order", "procure": "purchase order",
    "procurement": "purchase order", "buy": "purchase order", "reorder": "purchase order",
    "stockout": "stock out", "stockouts": "stock out", "inventory": "stock", "stocks": "stock", "stocked": "stock",
    "sold": "sales", "turnover": "revenue", "income": "revenue", "takings": "revenue",
    "client": "customer", "clients": "customer", "buyer": "customer", "buyers": "customer", "customers": "customer", "account": "customer",
    "item": "product", "items": "product", "sku": "product", "skus": "product", "material": "product", "materials": "product", "goods": "product",
    "location": "warehouse", "locations": "warehouse", "site": "warehouse", "depot": "warehouse", "hub": "warehouse", "dc": "warehouse",
    "shipment": "shipment", "shipments": "shipment", "freight": "shipment", "shipping": "shipment", "delivery": "shipment", "deliveries": "shipment",
    "delayed": "late", "delay": "late", "delays": "late", "overdue": "late", "behind": "late",
    "cancelled": "cancel", "canceled": "cancel", "cancellation": "cancel", "cancellations": "cancel",
    "costs": "expense", "cost": "expense", "expenses": "expense", "spending": "expense", "spend": "expense",
    "profits": "profit", "margin": "profit", "earnings": "profit", "profitability": "profit",
    "versus": "compare", "vs": "compare", "against": "compare", "comparison": "compare", "comparing": "compare",
    "analysis": "analyze", "analyse": "analyze", "analyzing": "analyze", "analytics": "analyze", "analysing": "analyze", "insights": "analyze", "insight": "analyze",
    "trends": "trend", "trending": "trend", "trajectory": "trend", "history": "trend", "evolution": "trend", "pattern": "trend",
    "display": "show", "view": "show", "open": "show", "navigate": "show", "go": "show", "take": "show", "bring": "show", "see": "show", "list": "show", "pull": "show", "fetch": "show", "get": "show", "visit": "show", "launch": "show",
    "add": "create", "make": "create", "raise": "create", "place": "create", "prepare": "create", "initiate": "create", "register": "create", "onboard": "create",
    "remove": "delete", "discard": "delete", "erase": "delete", "scrap": "delete",
    "authorise": "approve", "authorize": "approve", "approval": "approve", "approvals": "approve", "approved": "approve", "signoff": "approve",
    "reject": "decline", "denied": "decline", "deny": "decline",
    "decline": "fall", "drop": "fall", "dip": "fall", "decrease": "fall", "declined": "fall", "fell": "fall", "dropped": "fall", "lower": "fall", "slump": "fall", "down": "fall", "reduced": "fall",
    "increase": "rise", "increased": "rise", "spike": "rise", "surge": "rise", "jump": "rise", "grew": "rise", "growth": "rise", "higher": "rise", "up": "rise",
    "reason": "cause", "reasons": "cause", "caused": "cause", "causes": "cause", "driver": "cause", "drivers": "cause", "behind": "cause",
    "best": "top", "leading": "top", "highest": "top", "largest": "top", "biggest": "top", "greatest": "top",
    "worst": "bottom", "lowest": "bottom", "weakest": "bottom", "smallest": "bottom", "poorest": "bottom",
    "summary": "report", "brief": "report", "digest": "report", "overview": "report", "recap": "report",
    "firm": "supplier", "region": "region", "regions": "region", "market": "region", "markets": "region", "territory": "region",
    "cheapest": "price", "pricing": "price", "prices": "price", "cost-effective": "price", "affordable": "price",
    "shortage": "low", "depleted": "low", "scarce": "low", "short": "low",
}


def stem(w: str) -> str:
    if len(w) <= 3 or not w.isalpha():
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


CANON = {}
for _k, _v in _CANON_SRC.items():
    CANON[stem(_k)] = [stem(x) for x in _v.split()]
CANON["compare"] = ["compare"]


def words(text: str):
    t = text.lower().replace("&", " and ").replace("’", "'").replace("'s ", " ").replace("'", "").replace("-", " ")
    return re.findall(r"[a-z0-9$]+", t + " ")


def stems(text: str):
    """Plain stems (used for entity matching)."""
    return [stem(w) for w in words(text) if w not in STOP or w in KEEP]


def canon_tokens(text: str):
    """Stems with domain synonyms folded to a canonical stem (used for intent / metadata retrieval)."""
    out = []
    for s in stems(text):
        out.extend(CANON.get(s, [s]))
    return out


W_UNI, W_BI, W_CHAR = 1.0, 1.3, 0.3


def features(text: str) -> Counter:
    toks = canon_tokens(text)
    f = Counter()
    for t in toks:
        f["w:" + t] += W_UNI
        if len(t) >= 4:
            padded = f"#{t}#"
            for i in range(len(padded) - 2):
                f["c:" + padded[i:i + 3]] += W_CHAR
    for a, b in zip(toks, toks[1:]):
        f[f"b:{a}_{b}"] += W_BI
    return f


class Index:
    def __init__(self):
        self.docs = []
        self._built = False

    def add(self, doc_id: str, text: str, kind: str, **meta):
        self.docs.append({"id": doc_id, "text": text, "kind": kind, "meta": meta})
        self._built = False

    def build(self):
        feats = [features(d["text"]) for d in self.docs]
        df = Counter()
        for f in feats:
            df.update(f.keys())
        n = len(self.docs)
        self.idf = {k: math.log(1 + n / v) for k, v in df.items()}
        self.inv = defaultdict(list)
        for i, f in enumerate(feats):
            vec = {k: (1 + math.log(v)) * self.idf[k] for k, v in f.items()}
            norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
            for k, x in vec.items():
                self.inv[k].append((i, x / norm))
        self._built = True

    def search(self, query: str, k=5, kinds=None):
        """-> [(doc, cosine score)] best first."""
        if not self._built:
            self.build()
        qf = features(query)
        qvec = {key: (1 + math.log(v)) * self.idf.get(key, math.log(1 + len(self.docs))) for key, v in qf.items()}
        qnorm = math.sqrt(sum(x * x for x in qvec.values())) or 1.0
        scores = defaultdict(float)
        for key, qx in qvec.items():
            for i, dx in self.inv.get(key, ()):
                scores[i] += (qx / qnorm) * dx
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        out = []
        for i, s in ranked:
            d = self.docs[i]
            if kinds and d["kind"] not in kinds:
                continue
            out.append((d, s))
            if len(out) >= k:
                break
        return out
