# Demo script (~10 minutes)

**Persona:** Michael Smith, Operations Manager. **Story:** “I run the distribution arm. Instead of living in 10 screens, I tell
the assistant what I need — it navigates, analyses and prepares the work; I approve.” Data is synthetic, as of **2026-10-10**.

Start: `python run.py` → http://127.0.0.1:8000 → *Fill demo credentials* → Sign in.

| # | Say (in the assistant panel, or the top “Ask anything” bar) | What to point out |
|---|---|---|
| 1 | **“what page am I on”** | Page awareness: purpose, live filters, headline figures *as shown*, widgets, filters it can set. Each page/widget has an id (`data-page-id`, `data-widget-id`). |
| 2 | **“show revenue trend for the last 12 months”** | It opened **Analytics** and configured metric / group / range for you (filters flash). Dashed last point = month-to-date, excluded from the statistics. A chip offers *“Why did revenue change in Mar 2026?”* |
| 3 | **“why did revenue drop in March 2026”** | The business story: Metals −64 %, cancellations 4.8 → 14 %, on-time 93 → 70 %, Global Metals / Steel Supplies POs ~12.5 days late. Every bullet has a **source chip** — click one to open the exact evidence. “Likely cause” is hedged as a pattern, not proof. |
| 4 | **“compare revenue this month vs last month”** → **“and for Europe?”** | Like-for-like comparison (same elapsed days). The follow-up inherits metric and period. |
| 5 | **“which products are likely to run out in 14 days”** | Forecast table + Inventory sorted by days-left. |
| 6 | **“Analyze inventory items that may run out in the next 14 days and create purchase order drafts with best supplier options”** | The hero: visible plan steps, best supplier per product (price / lead time / on-time / rating), orders sized to ~45 days, grouped by supplier. **Nothing saved yet.** Open *details* under the answer to show routing, retrieved components and per-tool ms. Click **Create 8 drafts** → lands on Procurement (Draft). |
| 7 | **“submit all draft purchase orders”** → confirm | ≤ $50k auto-approved; > $50k goes to **Approvals**. |
| 8 | **“approve all pending purchase orders”** | Role limit in action: Manager can't approve > $100k; the agent says which are left for an Admin. |
| 9 | **“create a purchase order for 500 steel rods from Global Metals”** | The **form is filled in front of you** (supplier, product, qty, price list, expected date, total). Press *Create purchase order* yourself — the chat's duplicate confirm retires itself. |
| 10 | **“open inventory, suppliers and procurement in new tabs”** | Workspace tabs; deep links in the URL. |
| 11 | **“compare top 3 suppliers for steel with best pricing”** | Semantic grouping (“steel” → 3 real products), ranked table, recommended supplier, one-click follow-up PO. |
| 12 | **“delete all draft POs”** | Destructive confirmation; only drafts can ever be deleted. Cancel it. |
| 13 | **“delete all customers”**, **“email the CEO”**, **“flibber the gronk”** | Explicit refusal for unsupported actions; a clarification (not a guess) for gibberish. 0 invalid actions in the benchmark. |
| 14 | Open **Agent Activity** | Audit log of everything the assistant did (tier, intent, latency, outcome) and the benchmark scorecard (`python -m eval.run_eval` to refresh). |

**Talking points for the business audience:** one place to ask, act and audit; reads are instant, writes are always previewed and
confirmed; every number is traceable to a view; the assistant is bounded by the same roles and approval limits as the user.

**Be upfront about:** synthetic data; offline mode (no LLM) understands well-formed business phrasing but not every idiom or typo
(that is what the optional Claude tier is for); the held-out accuracy figures in the README; demo-grade security.
