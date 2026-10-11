# BizPilot AI — a context-aware assistant embedded in a supply-chain & business-operations app

An AI companion that lives **inside** the application: it knows every page, widget, filter, form, API and record, and operates
them for you — navigating, setting filters, filling forms, analysing (trends, comparisons, *why*), and preparing changes that you confirm.
Built for *Domain 5A — Context-Aware Application Agent*.

## Run it

```bash
pip install -r requirements.txt      # fastapi, uvicorn (Python 3.10+)
python run.py                        # seeds 24 months of synthetic data on first run, serves http://127.0.0.1:8000
```

Sign in with the demo manager **michael@bizpilot.demo / demo1234** (Admin: `priya@bizpilot.demo`, same password).
Walk-through: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md). Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```bash
python -m unittest discover -s tests -v    # 28 tests (own temp DB)
python -m eval.run_eval --failures         # benchmark; writes eval/results.json (shown on the Agent Activity page)
python run.py --reseed                     # regenerate the dataset (fixed as-of date 2026-10-10 so the documented numbers reproduce;
                                           #   override with BIZPILOT_AS_OF=YYYY-MM-DD — story months are relative to it)
```

Optional LLM tier (off by default; everything works offline): `set ANTHROPIC_API_KEY=...` (optionally `BIZPILOT_LLM_MODEL`, default `claude-haiku-5-5`).
It is used only when the fast path is unsure, only to *normalise the request*; the result goes through the same parser, validators and confirmations.

## What it does

* **10 pages** from one metadata registry: Dashboard, Inventory, Suppliers, Procurement, Sales & Orders, Logistics, Finance, Analytics, Approvals, Agent Activity.
* **Assistant** (right panel, `Ctrl+K`; also the top “Ask anything” bar): 25 intents —
  navigate/filter/sort/new tabs · page awareness · KPIs · trends · period comparison · *why did it change* (with linked evidence) · rankings ·
  stock-out forecast · supplier comparison · entity lookup · executive report · create / submit / approve / receive / delete POs ·
  stock & reorder changes · order status · supplier add/hold — plus follow-ups and explicit refusal of unsupported actions.
* **Every write is previewed and confirmed** (before → after, warnings, role limits, $50k approval rule). Delete: draft POs only.
* **Low latency**: no browser automation, no LLM on the fast path; p50 **2 ms**, p95 **12 ms** end-to-end in-process (see caveats).
* **Auditable**: `agent_audit` table + Agent Activity page.

## Honest status

| Measured (see `eval/results.json`) | |
|---|---|
| Intent routing on a **fresh held-out set** (69 utterances, run once) | **78.3 %** (first run of the earlier held-out set: 75.7 %) |
| Same, after the failures were fixed generically (tuned on, not a generalisation claim) | 100 % on 199 |
| Multi-step scenarios verified in the DB · numeric answers vs independent SQL | 10/10 · 6/6 |
| Invalid UI actions · writes without confirmation | 0/268 · 0/268 |
| Retrieval MRR@10 (369 docs) · with +600 hard distractors | 0.95 · 0.83 |

* The fast path misses typos and idioms (it asks instead of guessing). The LLM tier covers that in principle but **has not been run against the live API** (no key was available); it is unit-tested with a stubbed transport.
* All corpora and data are **synthetic and author-written**; no real-user study. Retrieval is lexical-semantic (no dense embeddings).
* Demo-grade security (fixed secret, demo credentials, SameSite-only CSRF, in-memory sessions). Single process; SQLite.
* Left out vs. the original mock-ups: Reports and Settings pages, Google sign-in, voice input.

## Layout

```
server/  db · seed · periods · analytics · pages (metadata+widgets) · ops (API catalog) · nlu · retrieval · intents · agent(kit/handlers) · llm · main
web/     index.html · css/app.css · js/{app,page,widgets,charts,forms,agent,api,ui}.js     (vanilla ES modules, no build step)
eval/    cases (dev) · cases_heldout (v1) · cases_heldout2 (fresh) · run_eval.py · results*.json
tests/   test_app.py
docs/    ARCHITECTURE.md · DEMO_SCRIPT.md
```
