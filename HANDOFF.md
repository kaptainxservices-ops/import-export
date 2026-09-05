# Handoff — state of the system, and what deployment needs

Written 6 September 2026. Read this first in a new conversation; `README.md` has the
full design reasoning, this has the state and the path to production.

---

## What it is

Supplier and buyer emails arrive → are parsed, normalised and stored → appear on a
shared filterable board → demand is matched against supply automatically.

The client (TVD Services) is a **pure broker** in the global secondary electronics
trade. They hold no stock. 20–30 emails a day.

```
Gmail ──n8n──▶ POST /ingest ──▶ pipeline ──▶ Supabase (Postgres + RLS)
                (token-gated)      │                    ▲
                                   │                    │
                          deterministic parse      React dashboard
                          + LLM only where          (Vite, port 5173)
                          rules cannot decide
```

**Stack:** FastAPI on Render (Python **3.11.9**, pinned — see gotchas) · Supabase
Postgres with row-level security · React 18 + Vite 6 + TypeScript · n8n for the inbox.

---

## Current state — verified today

| | |
|---|---|
| Tests | **608 passing**, ruff clean, TypeScript clean |
| Sample corpus | 45 real emails, all accepted by `check_writes.py` |
| Migrations | 9 (`0001`–`0009`) |
| Code | 51 Python files in `app/`, 25 test files, 18 files in `frontend/src` |
| Matching | 26 fillable requirements, 1,448 units allocated |
| Review load | 1.3% of rows flagged (was 37% before row/list separation) |

**Working end to end:** ingest → classify → extract → reconcile → persist → board →
match → review queue (row *and* list level) → deals with allocation guards → supplier
parsing settings → auth via Supabase.

---

## ⚠️ The deployment blocker, before anything else

**57 files are uncommitted.** The last commit is `f082ec2 feat: matching engine, both
directions`. Everything after it exists only on this laptop:

- the entire API layer — `app/api/{deals,deps,matches,review,suppliers}.py`
- `app/brain/{confidence,deals,quoting}.py` and all of `app/brain/llm/`
- **the entire `frontend/` directory** (untracked)
- migrations `0007`, `0008`, `0009`
- `supabase/setup_tenant.sql`, all of `demo/`, all of `n8n/`
- 11 test files
- 25 modified files on top of that

**Render deploys from GitHub.** Until this is committed and pushed, deploying
redeploys the matching engine and nothing else. This is step one and everything else
depends on it.

```bash
cd C:\Users\shaha\dev\import-export
git status                 # confirm .env is NOT listed (it's gitignored — verify)
git add -A
git commit -m "feat: API layer, review queues, confidence, deals, dashboard"
git push origin main
```

**Check `.env` is not staged before committing.** It holds `SUPABASE_SECRET_KEY`,
which bypasses every RLS policy.

---

## Deployment path, in order

### 1. Database — run the migrations that aren't applied

`0008_deals.sql` and `0009_review_at_list_level.sql` have **not been run against
Supabase**. The deals screen and the list-level review queue will 500 without them.

Supabase → SQL Editor → paste each file → run, in numerical order.

### 2. Backend — Render

Already live at `https://import-export-sno5.onrender.com`. `render.yaml` is a
blueprint, so config comes from the repo; secrets are set in the dashboard.

Env vars to confirm are set (all marked `sync: false`):

| Key | Notes |
|---|---|
| `SUPABASE_URL` | `https://oltdwhgzmjugodmnplrg.supabase.co` |
| `SUPABASE_SECRET_KEY` | the `sb_secret_…` key — never in the repo or frontend |
| `INGEST_TOKEN` | must match what n8n sends |
| `DASHBOARD_ORIGIN` | **the deployed frontend URL** — CORS fails without it |
| `ANTHROPIC_API_KEY` | currently empty; falls back to `LLM_*` |
| `LLM_BASE_URL` / `LLM_API_KEY` | Groq, temporary |

Verify:

```
/health        → {"status":"ok"}                    process alive
/health/ready  → {"database":"reachable"}           can reach Supabase
```

These are deliberately separate — if `/health` touched Supabase, a brief outage would
make Render restart the service in a loop.

### 3. Frontend — no deploy target exists yet

`render.yaml` defines **only the backend**. The dashboard has never been deployed.
Add a static site (Render, Vercel or Netlify — all equivalent here):

- build: `npm ci && npm run build` · publish: `frontend/dist` · root: `frontend`
- env: `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (the `sb_publishable_…`
  one — publishable is safe in a browser precisely because RLS is on), `VITE_API_URL`
  pointing at the Render backend
- then set `DASHBOARD_ORIGIN` on the backend to this site's URL and redeploy

### 4. Supabase auth config

- **Authentication → URL Configuration → Site URL** is `localhost:3000`; the app runs
  on **5173**. Magic links land on a dead port. Set it to the deployed URL, and add
  `http://localhost:5173/**` to Redirect URLs.
- **Custom SMTP.** The built-in mailer is capped at a few messages an hour — password
  resets and magic links both die on `email rate limit exceeded`. Not usable for a
  real client.
- Password can be set without email: `python scripts/set_password.py <email>`
  (Admin API, prompts hidden, revokes existing sessions).

### 5. n8n — the inbox

`n8n/gmail-to-ingest.json` is written but **has never been run**. Import it, connect
Gmail OAuth, set the `X-Ingest-Token` header, activate. `n8n/README.md` has the steps.

Currently watching `hardikshah1165@gmail.com` (test). Swap to the client's real
mailbox at go-live.

---

## Known IDs

| | |
|---|---|
| Supabase project | `oltdwhgzmjugodmnplrg` |
| Tenant (test) | `b9316981-b732-4891-854f-3335205dd582` |
| Auth user | `47895e02-d6c5-4f28-94f6-b0c36dc0dc55` — `shahardik1165@gmail.com` |
| Role | `platform_owner` (`tenant_id` null; every RLS policy short-circuits on it) |
| Watched inbox | `hardikshah1165@gmail.com` |
| Backend | `https://import-export-sno5.onrender.com` |

`supabase/setup_tenant.sql` is safe to re-run and has the user id at the top.

---

## Running it locally

```bash
# backend
cd C:\Users\shaha\dev\import-export
.venv\Scripts\activate
uvicorn app.main:app --reload

# frontend (second terminal)
cd frontend
npm install
npm run dev                    # http://localhost:5173

# load the board
python scripts\load_samples.py --tenant b9316981-b732-4891-854f-3335205dd582 \
    --samples samples demo --wipe
```

Useful scripts: `check_llm.py` (is the model reachable), `check_writes.py` (would all
45 emails persist), `ping_ingest.py` (is `/ingest` alive), `demo_matching.py`,
`set_password.py`.

---

## Not built yet

**Blocks go-live:**

- Outbound email — the system reads but never replies. Phase 6.
- Multi-user: roles, lead claiming, activity log. Only the owner login exists.
- Settings screen — staleness, currency defaults are DB-only today.
- Alerts and the "what changed today" diff.

**Wanted, not blocking:** counterparty profiles with price trend · open a deal
directly from a match · saved views · export · learned vocabulary mapping (**1,014
rows still match no catalogue entry** — the biggest single accuracy win left).

**Outstanding from the client — cannot be invented:**

1. The **ranked grade scale**. Grade is parsed but never ordered, so "A-" vs "B+" is
   not comparable and the board leaves it blank rather than guessing on goods worth
   hundreds a unit.
2. **Freight and duty per lane** — needed for landed-cost margin. Current margin is
   gross.
3. **Real mailbox access.**

---

## Design rules — do not undo these

Each was paid for with a bug.

- **The tenant is never taken from the request.** Resolved server-side from the
  Supabase session token via `profiles`. Isolation is enforced in Postgres by RLS,
  never in application code.
- **The model never supplies data.** It picks a label from a closed set or a column
  index; code verifies the answer against the rows. It cannot hallucinate a quantity.
  6 of 45 emails hold 473k of 652k characters — deterministic parsing does the work.
- **`ask()` returns `None` on every failure** — no key, no package, bad key, rate
  limit, outage, timeout. Absent model = review queue, never a crash.
- **Insertions are harmless, closures are destructive.** Reconciliation only closes
  rows on `completeness=True`; `False` and `None` both forbid it.
- **`description_key` (strict, identity) and `match_key` (loose, matching) are
  deliberately different keys.** Do not merge them.
- **`needs_review` is `bool(self.reasons)`, not `overall < THRESHOLD`.** A low score
  means something was inferred; it does not follow that showing the row to a human
  helps. Merging these took flagging from 37% to 1.3%.
- **Category is a hard gate in matching.** Without it a MagSafe case matched an
  iPhone.
- **The brand word is stripped from `match_key`** — `iphone 15` vs `apple iphone 15`
  broke every match.
- Repository protocol: the whole pipeline is testable in memory, no database.

**Gotchas already paid for:** Python is pinned to 3.11.9 because Render defaults to
3.14, which has no `pydantic-core` wheel — the error names pydantic but the cause is
the interpreter. `pydantic` 2.13.4 / `pydantic-settings` 2.15.0 / `supabase` 2.31.0
were resolved *together*; bumping one breaks the set. supabase-py below 2.31 rejects
`sb_secret_…` keys as malformed JWTs. The LLM client uses stdlib `urllib`, not httpx,
deliberately — two Render deploys were lost to pip resolution. It sends a real
`User-Agent` because `Python-urllib/3.11` is on Cloudflare's bot list (403, error
1010).

---

## Working agreement

Claude writes all the code. Hardik runs anything needing his credentials. Step by
step — he confirms or tests each step, then says "next".
