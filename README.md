# import-export

Backend for the iPhone trading automation. Reads buyer and seller emails, extracts
line items, normalises them, and serves a filterable board with matching.

## Architecture

n8n is plumbing. This backend is the brain. That split is deliberate and load-bearing:

```
n8n Cloud            watch mailbox, strip quoted history, attachments -> text/rows,
                     POST to this backend, send outbound mail
        |
        v
this backend         classify, extract, normalise, reconcile, match, persist
(FastAPI on Render)
        |
        v
Supabase             Postgres, multi-tenant RLS, auth, realtime
        |
        v
React on Vercel      Sellers, Buyers, Matches, source-email drawer
```

**No trading logic lives in n8n.** No "if grade contains A" nodes. If a rule is about
the trade, it belongs in `app/brain/`.

## Layout

```
app/
  main.py            FastAPI app assembly
  config.py          settings from env / .env
  api/               HTTP surface only — no business logic
    health.py        GET  /health   (open, for Render + UptimeRobot)
    ingest.py        POST /ingest   (token-guarded, n8n's entry point)
  schemas/           the contracts: what n8n sends, what extraction returns
  brain/             classification, extraction, normalisation  (Phase 2)
  db/                Supabase access                            (Phase 1.10)
tests/
samples/             real client emails — gitignored, never committed
```

## Running it locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
copy .env.example .env          # then fill in INGEST_TOKEN at minimum
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/health — expect `{"status":"ok",...}`.
API docs are at http://127.0.0.1:8000/docs.

## Tests

```bash
pytest
```

## Two rules that are cheap now and expensive later

1. **Secrets never enter git.** `.gitignore` was the first commit. The Supabase
   service role key lives only in Render's environment, never in the frontend.
2. **Insertions are harmless, closures are destructive.** Any code path that marks
   an offer sold or overwrites a price needs a guard and a history row. See
   `app/brain/` when it lands.
