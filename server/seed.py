"""Synthetic 24-month tenant for a supply-chain / business-operations app.

Deterministic (seeded). Stories are planted in the *data* only — the agent has no knowledge of them and has to
discover them through analysis:
  * a metals supply disruption 7 months before "today" (late inbound POs, cancelled metals orders, freight spike)
  * a November peak (seasonality, packaging +)
  * an APAC demand surge 2-3 months before "today"
  * a handful of low / out-of-stock SKUs and open purchase orders so the live demos have something to act on.
"""
import math
import os
import random
from datetime import date, timedelta

from . import db
from .auth import hash_password

SEED = 42
DEFAULT_AS_OF = "2026-10-10"      # "today" of the demo tenant; override with BIZPILOT_AS_OF=YYYY-MM-DD

WAREHOUSES = [("Chicago DC", "Chicago", "North America"), ("Dallas DC", "Dallas", "North America"),
              ("Rotterdam Hub", "Rotterdam", "Europe"), ("Singapore Hub", "Singapore", "APAC")]
REGIONS = {"North America": 0.40, "Europe": 0.28, "APAC": 0.24, "LATAM": 0.08}
COUNTRY_BY_REGION = {"North America": ["USA", "Canada", "Mexico"], "Europe": ["Germany", "Netherlands", "France", "Spain"],
                     "APAC": ["Singapore", "Australia", "Japan", "India"], "LATAM": ["Brazil", "Chile", "Colombia"]}
SUPPLIER_COUNTRIES = ["USA", "India", "China", "Germany", "Vietnam", "Mexico", "Netherlands", "Singapore", "Turkey", "Brazil"]
CARRIERS = ["FastFreight", "OceanLink", "AirSwift", "RoadRunner Logistics"]
SEGMENTS = {"Enterprise": (0.2, 60), "Wholesale": (0.5, 45), "Retail": (0.3, 30)}

# name, category, unit, base cost, popularity weight
PRODUCTS = [
    ("Steel Rod", "Metals", "kg", 32, 5), ("Plastic Resin", "Plastics & Polymers", "kg", 25, 4),
    ("Copper Wire", "Metals", "m", 54, 3.5), ("Aluminum Sheet", "Metals", "sheet", 46, 3),
    ("Stainless Steel Pipe", "Metals", "pcs", 78, 1.2), ("Brass Fittings", "Metals", "pcs", 21, 1.2),
    ("Galvanized Steel Sheet", "Metals", "sheet", 39, 1.4), ("Titanium Rod", "Metals", "kg", 140, 0.4),
    ("Iron Casting Blocks", "Metals", "pcs", 64, 0.8),
    ("PVC Pipe", "Plastics & Polymers", "pcs", 14, 1.5), ("Polyethylene Film", "Plastics & Polymers", "roll", 9, 1.4),
    ("Nylon Granules", "Plastics & Polymers", "kg", 28, 1.0), ("Acrylic Sheet", "Plastics & Polymers", "sheet", 35, 0.9),
    ("ABS Pellets", "Plastics & Polymers", "kg", 22, 1.1), ("Silicone Sealant", "Plastics & Polymers", "pcs", 7, 1.3),
    ("Electrical Switchgear", "Electrical", "pcs", 120, 0.5), ("Circuit Breakers", "Electrical", "pcs", 45, 1.2),
    ("LED Driver Module", "Electrical", "pcs", 18, 1.6), ("Capacitor Pack", "Electrical", "pack", 12, 1.5),
    ("Relay Module", "Electrical", "pcs", 16, 1.2), ("Transformer Coil", "Electrical", "pcs", 88, 0.6),
    ("Power Connector Set", "Electrical", "set", 10, 1.8), ("Insulated Wire Spool", "Electrical", "spool", 30, 1.3),
    ("Corrugated Box", "Packaging", "pcs", 2.2, 2.4), ("Bubble Wrap Roll", "Packaging", "roll", 11, 1.4),
    ("Pallet Wrap", "Packaging", "roll", 8, 1.6), ("Wooden Pallet", "Packaging", "pcs", 14, 1.5),
    ("Packing Tape", "Packaging", "roll", 3.5, 2.0), ("Foam Insert", "Packaging", "pcs", 5, 1.2),
    ("Label Roll", "Packaging", "roll", 6, 1.3),
    ("Ball Bearing", "Machinery Parts", "pcs", 9, 2.0), ("Hydraulic Hose", "Machinery Parts", "m", 26, 1.2),
    ("Gearbox Unit", "Machinery Parts", "pcs", 210, 0.3), ("Drive Belt", "Machinery Parts", "pcs", 15, 1.4),
    ("Fasteners Kit", "Machinery Parts", "kit", 18, 1.8), ("Pneumatic Valve", "Machinery Parts", "pcs", 52, 0.8),
    ("Servo Motor", "Machinery Parts", "pcs", 180, 0.35), ("Conveyor Roller", "Machinery Parts", "pcs", 41, 0.7),
    ("Hydraulic Seal Kit", "Machinery Parts", "kit", 24, 1.0),
    ("Industrial Lubricant", "Chemicals", "L", 19, 1.6), ("Epoxy Adhesive", "Chemicals", "kg", 17, 1.2),
    ("Cleaning Solvent", "Chemicals", "L", 12, 1.3), ("Anti-Corrosion Coating", "Chemicals", "L", 31, 0.9),
    ("Welding Flux", "Chemicals", "kg", 13, 1.0), ("Paint Primer", "Chemicals", "L", 16, 1.2),
    ("Coolant Fluid", "Chemicals", "L", 11, 1.1),
]
CATEGORIES = ["Metals", "Plastics & Polymers", "Electrical", "Packaging", "Machinery Parts", "Chemicals"]
FIXED_SUPPLIERS = [("Global Metals Ltd.", "Metals", "USA"), ("Steel Supplies Co.", "Metals", "India"),
                   ("MetalWorks Inc.", "Metals", "Germany"), ("Prime Plastics", "Plastics & Polymers", "China"),
                   ("Asia Components", "Electrical", "Singapore")]
NAME_STEMS = {
    "Metals": ["Titan Alloys", "Ironclad Metals", "Forge Dynamics", "Apex Steelworks", "Nova Alloys", "Zenith Metals"],
    "Plastics & Polymers": ["PolyCore", "Enviro Polymers", "Vertex Plastics", "Omni Resins", "Nylex Industries", "Clearform"],
    "Electrical": ["VoltEdge", "Circuitry Hub", "Ampere Systems", "BrightWire", "Sparkline Electric", "Orbit Electronics"],
    "Packaging": ["PackRight", "BoxWorks", "WrapMasters", "Crate & Co.", "Ecopack Solutions", "Sealwell"],
    "Machinery Parts": ["Gearhead Industries", "MotionTech", "Precision Parts Co.", "HydroLink", "Torque Dynamics", "RotoWorks"],
    "Chemicals": ["ChemNova", "Reactiv Labs", "BlueFlask", "Solvex", "CoatPro", "Lubritech"],
}
CUSTOMER_STEMS = ["Apex", "Northwind", "Summit", "Bluebird", "Ironbridge", "Harbor", "Pioneer", "Keystone", "Meridian",
                  "Evergreen", "Falcon", "Granite", "Horizon", "Lakeside", "Monarch", "Orion", "Redwood", "Sterling",
                  "Trident", "Union"]
CUSTOMER_TAILS = ["Manufacturing", "Industries", "Retail", "Trading", "Fabrication", "Supply", "Logistics", "Works", "Group", "Distributors"]

MONTH_FACTOR = {1: 0.92, 2: 0.95, 3: 1.0, 4: 1.02, 5: 1.05, 6: 1.03, 7: 1.0, 8: 0.98, 9: 1.04, 10: 1.08, 11: 1.18, 12: 1.12}


def month_offset(d: date, ref: date) -> int:
    return (d.year * 12 + d.month) - (ref.year * 12 + ref.month)


def iso(d):
    return d.isoformat() if d else None


def seed(as_of: date | None = None, verbose=True):
    as_of = as_of or date.fromisoformat(os.environ.get("BIZPILOT_AS_OF", DEFAULT_AS_OF))   # fixed by default so the documented demo numbers reproduce
    rnd = random.Random(SEED)
    if db.DB_PATH.exists():
        for ext in ("", "-wal", "-shm"):
            p = db.DB_PATH.with_name(db.DB_PATH.name + ext)
            if p.exists():
                p.unlink()
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db._meta_cache.clear()
    if hasattr(db._local, "conn"):
        db._local.conn = None
    conn = db.get_db()
    db.init_schema(conn)
    cur = conn.cursor()

    first_month = as_of.replace(day=1)
    y, m = first_month.year, first_month.month - 24
    while m <= 0:
        y, m = y - 1, m + 12
    start = date(y, m, 1)
    disruption_off, apac_offs = -7, (-3, -2)

    cur.execute("INSERT INTO meta VALUES('as_of',?)", (as_of.isoformat(),))
    cur.execute("INSERT INTO meta VALUES('data_start',?)", (start.isoformat(),))

    # users --------------------------------------------------------------------------------------------------------
    for email, name, role, limit in [("michael@bizpilot.demo", "Michael Smith", "Manager", 100000),
                                     ("priya@bizpilot.demo", "Priya Nair", "Admin", 10_000_000)]:
        salt, h = hash_password("demo1234")
        cur.execute("INSERT INTO users(email,name,role,approval_limit,salt,pw_hash) VALUES(?,?,?,?,?,?)",
                    (email, name, role, limit, salt, h))

    for i, (n, c, r) in enumerate(WAREHOUSES, 1):
        cur.execute("INSERT INTO warehouses VALUES(?,?,?,?)", (i, n, c, r))

    # suppliers ----------------------------------------------------------------------------------------------------
    supplier_rows, names_used = [], set()
    for name, cat, country in FIXED_SUPPLIERS:
        supplier_rows.append((name, cat, country)); names_used.add(name)
    per_cat = {c: 8 for c in CATEGORIES}
    for name, cat, _ in FIXED_SUPPLIERS:
        per_cat[cat] -= 1
    for cat in CATEGORIES:
        stems = NAME_STEMS[cat][:]
        rnd.shuffle(stems)
        for stem in stems:
            if per_cat[cat] <= 0:
                break
            if stem not in names_used:
                supplier_rows.append((stem, cat, rnd.choice(SUPPLIER_COUNTRIES))); names_used.add(stem)
                per_cat[cat] -= 1
    # categories need ~8 suppliers; fill remaining with numbered variants
    for cat in CATEGORIES:
        while per_cat[cat] > 0:
            nm = f"{rnd.choice(NAME_STEMS[cat]).split()[0]} {rnd.choice(['Group', 'Partners', 'Intl', 'Exports'])}"
            if nm not in names_used:
                supplier_rows.append((nm, cat, rnd.choice(SUPPLIER_COUNTRIES))); names_used.add(nm)
                per_cat[cat] -= 1
    supplier_rows = supplier_rows[:48]
    for i, (name, cat, country) in enumerate(supplier_rows, 1):
        status = "active" if i != 17 and i != 33 else ("on_hold" if i == 17 else "inactive")
        email = "sales@" + "".join(ch for ch in name.lower() if ch.isalnum()) + ".com"
        cur.execute("INSERT INTO suppliers VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (i, name, country, cat, round(rnd.uniform(3.2, 4.9), 1), round(rnd.uniform(80, 99), 1),
                     rnd.randint(5, 22), rnd.choice(["Net 30", "Net 45", "Net 60", "Advance 20%"]), status, email,
                     iso(start + timedelta(days=rnd.randint(0, 200)))))
    suppliers = db.q("SELECT * FROM suppliers")
    sup_by_cat = {c: [s for s in suppliers if s["category"] == c] for c in CATEGORIES}
    metals_disrupted = {s["id"] for s in suppliers if s["name"] in ("Global Metals Ltd.", "Steel Supplies Co.")}

    # products + supplier prices ---------------------------------------------------------------------------------
    pops, sku_used = {}, set()
    for i, (name, cat, unit, cost, pop) in enumerate(PRODUCTS, 1):
        sku = {1: "SR-001", 2: "PR-002", 3: "CW-003", 4: "AS-004"}.get(i)
        if not sku:
            ini = "".join(w[0] for w in name.replace("&", "").split())[:2].upper()
            sku = f"{ini}-{i:03d}"
        sku_used.add(sku)
        price = round(cost * rnd.uniform(1.32, 1.62), 2)
        cands = [s for s in sup_by_cat[cat] if s["status"] == "active"]
        offered = rnd.sample(cands, min(len(cands), rnd.randint(3, 5)))
        if i in (1, 2, 3, 4):  # flagship products are always offered by the "famous" suppliers
            fixed = {1: "Global Metals Ltd.", 2: "Prime Plastics", 3: "Global Metals Ltd.", 4: "Steel Supplies Co."}[i]
            fs = next(s for s in suppliers if s["name"] == fixed)
            if fs not in offered:
                offered[0] = fs
        primary = offered[0]
        cur.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?)", (i, sku, name, cat, unit, cost, price, primary["id"]))
        for s in offered:
            cur.execute("INSERT INTO supplier_prices VALUES(?,?,?,?,?)",
                        (s["id"], i, round(cost * rnd.uniform(0.86, 1.14), 2), rnd.choice([50, 100, 200, 500]),
                         max(3, int(s["lead_time_days"] + rnd.randint(-3, 4)))))
        pops[i] = pop
    products = db.q("SELECT * FROM products")

    # customers ----------------------------------------------------------------------------------------------------
    customers, cust_names = [], set()
    while len(customers) < 120:
        nm = f"{rnd.choice(CUSTOMER_STEMS)} {rnd.choice(CUSTOMER_TAILS)}"
        if nm in cust_names:
            continue
        cust_names.add(nm)
        region = rnd.choices(list(REGIONS), weights=list(REGIONS.values()))[0]
        seg = rnd.choices(list(SEGMENTS), weights=[v[0] for v in SEGMENTS.values()])[0]
        customers.append((len(customers) + 1, nm, seg, region, rnd.choice(COUNTRY_BY_REGION[region])))
    cur.executemany("INSERT INTO customers VALUES(?,?,?,?,?)", customers)
    cust_by_region = {r: [c for c in customers if c[3] == r] for r in REGIONS}
    cust_weight = {c[0]: rnd.paretovariate(2.2) for c in customers}
    wh_by_region = {"North America": [1, 2], "Europe": [3], "APAC": [4], "LATAM": [2]}
    cat_products = {c: [p for p in products if p["category"] == c] for c in CATEGORIES}

    # sales orders -------------------------------------------------------------------------------------------------
    order_id = item_id = ship_id = inv_id = 0
    orders, items, ships, invoices = [], [], [], []
    d = start
    seq = 0
    while d <= as_of:
        off = month_offset(d, as_of)
        base = 9.5 if d.weekday() < 5 else 4.0
        lam = base * MONTH_FACTOR[d.month] * (1.012 ** (off + 24))
        if off == disruption_off:
            lam *= 0.88            # knock-on: customers stop ordering what we cannot ship
        n = _poisson(rnd, lam)
        for _ in range(n):
            region = rnd.choices(list(REGIONS), weights=[REGIONS[r] * (1.55 if (r == "APAC" and off in apac_offs) else 1)
                                                         for r in REGIONS])[0]
            cust = rnd.choices(cust_by_region[region], weights=[cust_weight[c[0]] for c in cust_by_region[region]])[0]
            n_items = rnd.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0]
            chosen = {}
            for _i in range(n_items):
                w = []
                for p in products:
                    wt = pops[p["id"]]
                    if d.month == 11 and p["category"] == "Packaging":
                        wt *= 1.9
                    if off == disruption_off and p["category"] == "Metals":
                        wt *= 0.5
                    w.append(wt)
                p = rnd.choices(products, weights=w)[0]
                target = math.exp(rnd.gauss(math.log(330), 0.62))
                qty = max(1, round(target / p["unit_price"]))
                chosen[p["id"]] = (p, qty)
            order_id += 1
            seq += 1
            wh = rnd.choice(wh_by_region[region])
            lead = {"North America": 3, "Europe": 4, "APAC": 6, "LATAM": 7}[region] + rnd.randint(0, 2)
            promised = d + timedelta(days=lead)
            has_metals = any(p["category"] == "Metals" for p, _ in chosen.values())
            cancel_p = 0.035
            if off == disruption_off and has_metals:
                cancel_p = 0.4
            status_cancel = rnd.random() < cancel_p
            delay_p, delay_rng = 0.07, (1, 5)
            if off == disruption_off:
                delay_p, delay_rng = 0.30, (2, 9)
            delay = rnd.randint(*delay_rng) if rnd.random() < delay_p else -rnd.choice([0, 0, 1, 2])
            delivered = promised + timedelta(days=delay)
            shipped = d + timedelta(days=rnd.randint(1, 2))
            drift = 1 + 0.003 * (off + 24)
            total = 0.0
            for p, qty in chosen.values():
                price = round(p["unit_price"] * drift * rnd.uniform(0.96, 1.04), 2)
                item_id += 1
                items.append((item_id, order_id, p["id"], qty, price))
                total += qty * price
            if status_cancel:
                status, deliv = "cancelled", None
            elif delivered <= as_of:
                status, deliv = "delivered", delivered
            elif shipped <= as_of:
                status, deliv = "shipped", None
            elif d < as_of:
                status, deliv = "processing", None
            else:
                status, deliv = "pending", None
            orders.append((order_id, f"SO-{100000 + seq}", cust[0], region, wh, status, iso(d), iso(promised), iso(deliv),
                           round(total, 2)))
            if status in ("shipped", "delivered"):
                ship_id += 1
                eta = promised
                if status == "delivered":
                    sstatus, delay_days = "delivered", max(0, (deliv - eta).days)
                else:
                    late = (as_of - eta).days
                    sstatus, delay_days = ("delayed", late) if late > 0 else ("in_transit", 0)
                ships.append((ship_id, order_id, rnd.choice(CARRIERS), sstatus, iso(shipped), iso(eta), iso(deliv), delay_days,
                              WAREHOUSES[wh - 1][1], region))
            if status == "delivered":
                inv_id += 1
                terms = SEGMENTS[cust[2]][1]
                paid = deliv + timedelta(days=max(1, int(rnd.gauss(terms - 6, 11)))) if rnd.random() < 0.96 else None
                if paid and paid > as_of:
                    paid = None
                invoices.append((inv_id, order_id, cust[0], iso(deliv), iso(deliv + timedelta(days=terms)), iso(paid),
                                 round(total, 2)))
        d += timedelta(days=1)
    cur.executemany("INSERT INTO sales_orders VALUES(?,?,?,?,?,?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO so_items VALUES(?,?,?,?,?)", items)
    cur.executemany("INSERT INTO shipments VALUES(?,?,?,?,?,?,?,?,?,?)", ships)
    cur.executemany("INSERT INTO invoices VALUES(?,?,?,?,?,?,?)", invoices)

    # inventory (derived from recent demand so "days left" is realistic) ---------------------------------------------
    since = iso(as_of - timedelta(days=60))
    demand = {}
    for r in db.q("""SELECT i.product_id pid, o.warehouse_id wid, SUM(i.qty) u FROM so_items i JOIN sales_orders o ON o.id=i.order_id
                     WHERE o.order_date>=? AND o.status!='cancelled' GROUP BY 1,2""", (since,)):
        demand[(r["pid"], r["wid"])] = r["u"] / 60.0
    prod_lead = {p["id"]: db.scalar("SELECT AVG(lead_time_days) FROM supplier_prices WHERE product_id=?", (p["id"],), 10)
                 for p in products}
    for p in products:
        roll = rnd.random()
        if p["id"] in (1, 2, 3, 4):
            base_cover = rnd.uniform(4.5, 8.5)        # flagship metals/resin: deliberately about to run out
        elif roll < 0.05:
            base_cover = 0
        elif roll < 0.15:
            base_cover = rnd.uniform(6, 15)
        else:
            base_cover = rnd.uniform(40, 110)
        for wid in range(1, 5):
            dem = demand.get((p["id"], wid), 0.0)
            if dem == 0:
                dem = rnd.uniform(0.05, 0.4)
            cover = base_cover * rnd.uniform(0.8, 1.2)
            qty = int(dem * cover)
            reorder = int(math.ceil(dem * (prod_lead[p["id"]] + 7)))
            cur.execute("INSERT INTO inventory VALUES(?,?,?,?)", (p["id"], wid, qty, reorder))

    # expenses -----------------------------------------------------------------------------------------------------
    weekly = {}
    for o in orders:
        if o[5] != "cancelled":
            dd = date.fromisoformat(o[6])
            wk = dd - timedelta(days=dd.weekday())
            weekly[wk] = weekly.get(wk, 0) + o[9]
    shares = {"Raw Materials": 0.26, "Logistics": 0.115, "Manufacturing": 0.085, "Administrative": 0.06, "Other": 0.055}
    for wk, rev in sorted(weekly.items()):
        off = month_offset(wk, as_of)
        for cat, sh in shares.items():
            mult = rnd.uniform(0.92, 1.08)
            if off == disruption_off and cat == "Logistics":
                mult *= 1.45          # expedited freight during the disruption
            if off == disruption_off and cat == "Raw Materials":
                mult *= 1.12
            cur.execute("INSERT INTO expenses(date,category,amount) VALUES(?,?,?)", (iso(wk), cat, round(rev * sh * mult, 2)))

    # purchase orders ----------------------------------------------------------------------------------------------
    sp = {}
    for r in db.q("SELECT * FROM supplier_prices"):
        sp.setdefault(r["supplier_id"], []).append(r)
    sup_weight = {s["id"]: rnd.paretovariate(2.0) for s in suppliers}
    for sid in metals_disrupted:
        sup_weight[sid] *= 10          # the two big metals suppliers
    pos, po_items, approvals = [], [], []
    po_id = poi_id = 0
    yr_seq = {}
    wk = start
    open_cutoff = as_of - timedelta(days=3)
    while wk <= as_of:
        for _ in range(rnd.randint(5, 9)):
            cands = [s for s in suppliers if s["id"] in sp and s["status"] == "active"]
            s = rnd.choices(cands, weights=[sup_weight[c["id"]] for c in cands])[0]
            created = wk + timedelta(days=rnd.randint(0, 6))
            if created > as_of:
                continue
            rows = rnd.sample(sp[s["id"]], min(len(sp[s["id"]]), rnd.randint(1, 4)))
            po_id += 1
            yr_seq[created.year] = yr_seq.get(created.year, 0) + 1
            total, lead = 0.0, 0
            for r in rows:
                target = math.exp(rnd.gauss(math.log(7500), 0.85))
                qty = max(r["min_qty"], round(target / r["unit_price"]))
                poi_id += 1
                po_items.append((poi_id, po_id, r["product_id"], qty, r["unit_price"]))
                total += qty * r["unit_price"]
                lead = max(lead, r["lead_time_days"])
            total = round(total, 2)
            expected = created + timedelta(days=lead)
            off = month_offset(expected, as_of)
            delay = int(rnd.gauss(0.5, 1.8))
            if off == disruption_off and s["id"] in metals_disrupted:
                delay = rnd.randint(8, 16)
            received = expected + timedelta(days=delay)
            if rnd.random() < 0.03:
                status, rec = "cancelled", None
            elif received <= open_cutoff:
                status, rec = "received", received
            else:
                age = (as_of - created).days
                if age <= 5:
                    status, rec = rnd.choice(["draft", "draft", "pending_approval", "approved"]), None
                elif total > 50000 and age <= 9:
                    status, rec = "pending_approval", None
                else:
                    status, rec = rnd.choice(["approved", "ordered", "ordered"]), None
            if total > 50000 and status in ("draft",):
                status = "pending_approval"
            pos.append((po_id, f"PO-{created.year}-{yr_seq[created.year]:04d}", s["id"], status, iso(created), iso(expected),
                        iso(rec), total, "michael@bizpilot.demo" if rnd.random() < 0.6 else "priya@bizpilot.demo", None))
        wk += timedelta(days=7)
    # make sure the live demos have open work: recent drafts and approvals waiting on someone
    def _recent(pred):
        return sorted([i for i, p in enumerate(pos) if pred(p)], key=lambda i: pos[i][4], reverse=True)
    for i in _recent(lambda p: p[3] in ("approved", "ordered") and p[7] <= 50000)[:4]:
        pos[i] = pos[i][:3] + ("draft",) + pos[i][4:]
    for i in _recent(lambda p: p[3] in ("approved", "ordered") and p[7] > 50000)[:2]:
        pos[i] = pos[i][:3] + ("pending_approval",) + pos[i][4:]
    for p in pos:
        if (p[7] > 50000 and p[3] not in ("cancelled", "draft")) or p[3] == "pending_approval":
            ast = "pending" if p[3] == "pending_approval" else "approved"
            cdate = date.fromisoformat(p[4])
            approvals.append(("purchase_order", p[0], p[1], p[7], p[8], ast, "PO value above $50,000 requires approval" if p[7] > 50000 else "Submitted for approval",
                              p[4], "priya@bizpilot.demo" if ast == "approved" else None,
                              iso(cdate + timedelta(days=1)) if ast == "approved" else None))
    cur.executemany("INSERT INTO purchase_orders VALUES(?,?,?,?,?,?,?,?,?,?)", pos)
    cur.executemany("INSERT INTO po_items VALUES(?,?,?,?,?)", po_items)
    cur.executemany("INSERT INTO approvals(type,ref_id,ref_label,amount,requested_by,status,reason,created_at,decided_by,decided_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)", approvals)
    conn.commit()

    if verbose:
        print(f"as_of={as_of} window starts {start}")
        for t in ["suppliers", "products", "customers", "sales_orders", "so_items", "shipments", "purchase_orders",
                  "po_items", "expenses", "invoices", "approvals", "inventory"]:
            print(f"  {t:16s}{db.scalar(f'SELECT COUNT(*) FROM {t}'):>8,}")
    conn.close()
    db._local.conn = None


def _poisson(rnd, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1


if __name__ == "__main__":
    seed()
