# import-export

A trading desk's inbox, turned into a board.

Twenty to thirty emails arrive each day carrying offers and requirements — HTML tables,
prose, Excel attachments, forwarded WhatsApp screenshots, in seven languages. This system
reads every one of them, pulls out every line item, keeps the resulting list current on its
own, and works out which buyers and sellers fit together and what the spread is on each.

Built for a broker in the global secondary electronics trade. The broker never takes
ownership of stock, so every row is a claim about a third party's goods that can vanish
without notice — which is why almost every rule below is about *not* trusting the data too
far.

**Status — 3 September 2026.** Phases 1–5 built and passing. 608 tests, 84% coverage, ruff
clean. Outbound email, landed-cost margin and the admin panel are not built yet.

---

## Architecture

n8n is plumbing. This backend is the brain. That split is deliberate and load-bearing.

```
n8n Cloud              watch mailbox · strip quoted history · attachments -> rows
                       assemble payload · POST /ingest
        |
        v
FastAPI on Render      dedupe -> resolve sender -> classify -> extract -> normalise
(the brain)                   -> score confidence -> reconcile -> persist -> match
        |
        v
Supabase Postgres      multi-tenant, row-level security, auth, realtime
        |
        v
React on Vercel        Matches · Sellers · Buyers · Deals · Review · Suppliers
```

**No trading logic lives in n8n.** No "if grade contains A" nodes. n8n is a GUI: anything
living only inside it cannot be reviewed in a diff, cannot be tested, and will one day be
edited by someone in a hurry. If a rule is about the trade, it belongs in `app/brain/`.

**The LLM is a last resort, not the engine.** Everything that can be decided by code is
decided by code. The model is asked three narrow questions — which column is which, is this
email buying or selling, and nothing else — and each answer is checked against something
deterministic before it is believed. Code cannot hallucinate a quantity.

---

## What is built

### The brain — `app/brain/`

| Module | What it decides |
|---|---|
| `sender.py` | Who actually sent this. Three tiers: forwarded-header parsing, genuine envelope, and unattributable → review queue |
| `quoting.py` | Where the real message starts, without cutting a forwarded payload |
| `classify.py` | Offer or requirement. Deterministic WTS/WTB signals across seven languages; model fallback for the ambiguous minority |
| `completeness.py` | Is this the sender's complete stock, or a one-off? Three-valued — undecided forbids closure exactly as `False` does |
| `tables/` | Leaf-table extraction from HTML, `.xlsx` via openpyxl, legacy `.xls` via xlrd. Header-row detection, section-heading inheritance, float-artifact repair |
| `normalise/` | Brands, models, colours, grades, storage, quantity, region codes, currency and price basis. Multi-value line expansion (`Black / Blue / Grey — €125` → three offers) |
| `confidence.py` | How much of a row we read versus guessed. Below threshold, the row is held out of the live board |
| `reconcile.py` | What a new list means for what we already hold: refresh, update, close, or insert |
| `matching.py` | Both directions — fill a buyer from many suppliers, or place one lot across many buyers. Combinations, partial fills, near-misses, risk-adjusted ranking |
| `deals.py` | What a commitment may promise. Over-allocating one lot is refused; committing the same lot twice is warned about, not blocked |
| `llm/` | Dual-provider client, column mapping with corroboration, side classification over a closed answer set, per-tenant token and cost accounting |

### The HTTP surface — `app/api/`

16 routes across six routers. `/ingest` is token-guarded and deduplicates on `message_id`;
`/health` and `/health/ready` are open for Render and uptime monitoring. The rest —
`/matches/board`, `/matches/fill`, `/matches/place`, `/deals` with allocations,
`/review/queue`, `/review/resolve`, `/suppliers` — serve the dashboard. No business logic
lives in this layer.

### The database — `supabase/`

Nine migrations, 18 tables, five functions, ~1,460 lines of SQL. Tenancy and RLS; the
trading core (counterparties, emails, imports, offers, offer_changes); reference data
(grades and per-sender aliases, model catalogue, freight rates); outbound scaffolding;
usage accounting; deals and allocations.

Isolation lives in the database, not the API. A forgotten `WHERE` clause returns nothing
rather than leaking a broker's supplier prices to a competitor. `supabase/tests/rls_isolation.sql`
must print `RLS ISOLATION PASSED` before anything is built on top.

### The dashboard — `frontend/`

React + Vite + TypeScript, ~3,780 lines. Six tabs: Matches, Sellers, Buyers, Deals, Review,
Suppliers. Talks to Supabase directly with the **publishable** key — what a signed-in user
can read is decided by row-level security in Postgres, so a bug in the browser cannot leak
another tenant's prices. Filtering runs in Postgres, not the browser, because a busy morning
puts thousands of rows on the board.

The source-email drawer is the point of the whole screen. Every number on the board was read
out of an email by software; the first time a price looks wrong the trader will want to see
what the supplier actually wrote, and if he cannot, he stops believing the board.

### The inbox — `n8n/`

`gmail-to-ingest.json` — five nodes: poll every five minutes, convert attachments to rows,
build the payload, POST to `/ingest`, log the outcome. Fetch and deliver, nothing more.

### The demo — `demo/`

Six `.eml` files that go through the real pipeline, not fixtures. Three seller offers, two
buyer requests, one lift-maintenance notice that must be ignored. They exist to demonstrate
one specific thing: nobody has the 100 units Meridian wants, but Al Manar's 40 and Gulf
Cell's 60 together do — blended cost 918.80, margin 4,120 USD.

---

## Tech stack

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.11.9 (pinned) |
| API | FastAPI · uvicorn | 0.115.6 · 0.34.0 |
| Validation | pydantic · pydantic-settings | 2.13.4 · 2.15.0 |
| LLM | anthropic SDK, plus any OpenAI-compatible endpoint | 0.42.0 |
| Models | Haiku 4.5 to classify, Sonnet 5 to map columns | |
| Database client | supabase-py | 2.31.0 |
| Parsing | openpyxl · xlrd · beautifulsoup4 · lxml | 3.1.5 · 2.0.1 · 4.12.3 · 5.3.0 |
| Tests · lint | pytest · pytest-cov · httpx · ruff | 8.3.4 · 6.0.0 · 0.28.1 · 0.8.6 |
| Frontend | React · Vite · TypeScript · supabase-js | 18.3.1 · 6.4.3 · 5.7.2 · 2.112.2 |
| Backend hosting | Render (starter, Frankfurt) | |
| Database | Supabase Postgres, multi-tenant RLS | |
| Frontend hosting | Vercel *(planned)* | |
| Automation | n8n Cloud | |
| CI | GitHub Actions — ruff + pytest on every push and PR | |

The LLM layer supports two providers, chosen by which key is filled in. The
OpenAI-compatible path is written against `urllib` from the standard library rather than an
SDK — two Render deploys were already lost to pip resolving one package against another,
and a POST built out of the standard library cannot fail to install.

---

## Metrics

### Code

| | |
|---|---|
| Backend | 9,148 lines across 51 modules |
| Tests | 4,749 lines, 25 files |
| SQL | 1,457 lines, 9 migrations, 18 tables |
| Frontend | 3,781 lines, 10 components |
| **Tests passing** | **608 / 608** |
| **Coverage** | **84%** overall |
| Lint | ruff clean |
| API routes | 16 across 6 routers |

Coverage is highest where being wrong is expensive: `reconcile.py` 98%, `sender.py` 98%,
`tables/columns.py` 97%, `money.py` 96%, `pipeline.py` 96%, `matching.py` 95%,
`product.py` / `confidence.py` / `deals.py` / `quoting.py` 100%. The low numbers are
`supabase_repo.py` at 26% and `tables/reader.py` at 45% — both are I/O against services the
test suite deliberately does not call.

### The corpus the parser was built against

45 real client emails, analysed 7 August 2026 — 31 offers, 14 buyer requests.

| | |
|---|---|
| Total body text | 652,000 characters ≈ 163,000 tokens |
| Largest single email | 144,669 characters, one 2,235-row table |
| HTML table rows across the sample | 13,543 |
| Formats | 31 HTML tables, 9 prose, 3 attachment-only, 9 documents (6 xlsx, 1 legacy xls, 2 PDF), 1 photograph |
| Distinct suppliers identifiable | 21 |
| Rows carrying an EAN | ~54% |
| Emails using comma as decimal separator | 11 of 45 |
| Languages in surrounding prose | 7 |
| Brands | Samsung 27 emails, Apple 21, Motorola 17, Xiaomi 17, Pixel 15, Oppo 12, Sony 11, Honor 10, OnePlus 9 |

Those six largest emails are the whole argument for deterministic parsing. Sending them to a
model as raw text and asking for every row as JSON costs more in output tokens than input,
takes minutes per email, and is less accurate than code.

### Running cost

| | Provider | Per month |
|---|---|---|
| Reading email, drafting messages | Anthropic | $28 |
| Inbox watching and automation | n8n | $24 |
| Database and dashboard hosting | Supabase, Vercel | $20 |
| Email processing server | Render | $7 |
| Domain | | $1 |
| Matching | runs in Postgres | free |
| **Total** | | **$80** |

Anthropic is the only cost that scales with volume, which is why usage is metered per tenant
in `usage_events`. At ten clients the projection is ~$331/month, roughly $33 each.

### Repository

`kaptainxservices-ops/import-export` (private) · branch `main` · 12 commits, last
10 August 2026.

Phases 2–5 — the LLM layer, deals, review, quoting, suppliers, the entire frontend, the n8n
workflow and migrations 0007–0009 — are **built and passing but not yet committed**. That is
the first thing to fix.

---

## What is planned

### Phase 6 — outbound email

Schema exists (`0004_outbound.sql`: templates, outbound_messages, conversations); no code
yet.

- Template editor in the dashboard, in the client's own words
- AI personalisation of **wording only** — prices, quantities and specs are injected from
  the database as fixed values, so no figure can be hallucinated
- Send flow with mandatory draft review; nothing auto-sends
- Reply linking by watching Sent and matching on `In-Reply-To`
- Reply intent routing: **facts update the row, negotiations do not.** "Sold out" or "price
  is 890 now" updates the offer with history. A counter-offer opens a conversation and badges
  the row. Ambiguous defaults to negotiation, because that is the non-destructive direction
- A deliberately thin **In progress** screen: which offer, who, the messages, a note, mark
  done. No allocations, no margin maths — that is what blows the timeline

### Landed-cost margin

`freight_rates` exists in `0003`; matching currently warns about cross-border combinations
but does not price them. Where a route has rates, show landed margin; where it does not,
show the raw spread clearly labelled. **Never guess a freight figure.**

### Phase 7 — admin and go-live

Client list with health view (status, last email received, 24-hour volume, errors, cost
month-to-date), add/edit client, user creation against a tenant, usage and error views.
Then Sentry and UptimeRobot — so a dead pipeline is noticed by us, not by the client three
days later — end-to-end two-tenant isolation verification, and switching on the real mailbox.

### Also outstanding

- Deploy the frontend to Vercel and point the domain at it
- Set `ANTHROPIC_API_KEY` in Render — the LLM paths are written and tested but have never
  run against a live key
- Grade hierarchy, once the client supplies his scale ranked best to worst

### Later, on top of this — not rebuilding it

Price history from the offers already stored · a "what changed today" screen · supplier
reliability scores · price alerts · full deal tracking through to settlement · automatic
stock chasing, which turns staleness from an assumption into a verified fact.

### Product B

The full platform — multi-tenant, sold to other import/export businesses — is a clean
rebuild after v1 ships, not an evolution of this codebase. The only thing that carries
forward is knowledge: the sample emails, the measured extraction accuracy, the client's
vocabulary, and the list of formats that broke the parser.

---

## Blocked on the client

Five things, in order of how much they hold up:

1. **Grade scale, ranked best to worst.** Not what A, A+, B, 14-day and CPO mean — their
   order. Matching cannot decide whether A/A+ satisfies a request for A without it.
2. **Freight and duty per route.** What it costs to move 100 units Dubai to Brazil and what
   duty applies. Rough per-lane numbers are enough; without them margin is a raw spread.
3. **Mailbox provider and access, read *and* send.** Both scopes requested at once — a
   business tenant may need IT approval.
4. **His three most-used messages**, written as he would write them.
5. **Who on his team needs a login.**

---

## The rules that govern this code

These are not style preferences. Each one exists because getting it backwards costs a deal.

**Insertions are harmless. Closures are destructive.** A wrong new row is noise someone
ignores. Wrongly closing a live 120-unit lot removes real stock from the board and the broker
never learns the deal was there. So closure is hedged four ways — the list must be believed
complete, its row count must be plausible against that sender's history, it must not be
empty, and it must not be a duplicate import — and when any check is uncertain, nothing
closes.

**A supplier's complete list is the verification.** Suppliers send their entire current stock
every time, confirmed with the client. That single fact licences the disappearance rule —
present yesterday, absent today, therefore sold — and it is what makes the board
self-maintaining with no messages sent.

**Offer identity excludes price and quantity.** Identity is EAN when present, and brand +
description + capacity + colour when not. Price and quantity are what change; the same lot at
a new price is the same offer updated, not a new row.

**Never invent an exchange rate.** Where buyer and seller quote different currencies the
margin is left empty and the option is flagged. A guessed rate produces a plausible number
that is wrong, and nobody checks a plausible number.

**Never promise stock that does not exist.** A fill is capped at what is available and a
shortfall is stated rather than rounded away.

**Near-misses are surfaced, never silently dropped.** A buyer wanting grade A when only grade
B is available is a phone call, not a dead end.

**Undecided is not a third answer, it is a refusal.** An email whose sender cannot be
established, or whose side cannot be decided, is stored and flagged rather than guessed at.
A review queue costs somebody a click; a supplier's catalogue filed as buyer demand corrupts
two boards at once.

**Secrets never enter git.** `.gitignore` was the first commit. The Supabase secret key lives
only in Render's environment and never reaches a browser; `frontend/src/lib/supabase.ts`
refuses to start if handed one.

---

## Gotchas already paid for

Do not rediscover these.

- **European number formats.** `109,50` is 109.50 and `€1.079` is 1079. Decided per value
  with a per-supplier override. A 1000× error does not look like a bug — it looks like an
  extraordinary margin, which is exactly the kind of number a broker acts on before checking.
- **supabase-py must be ≥ 2.31.** 2.11 validates that the API key looks like a JWT and
  rejects the current `sb_secret_…` format before making any request. That upgrade forced
  pydantic to 2.13.4 and pydantic-settings to 2.15.0; all three were resolved together, not
  bumped one at a time.
- **Render defaults to Python 3.14**, which has no pydantic-core wheel and no writable cargo
  registry to build one. Pinned to 3.11.9 in `.python-version`. The error names pydantic-core;
  the cause is the interpreter.
- **PostgREST sends JSON literals.** `"now()"` stores those six characters, silently.
- **Take leaf tables, not outer wrappers.** BeautifulSoup's `get_text()` flattens descendants
  into every cell of a layout table.
- **Colour often lives in its own column** and is not repeated in the description. Ignoring it
  collapsed 27% of one supplier's rows into duplicates.
- **The forwarding marker is not a quote marker here.** 28 of 43 sample bodies contain
  `Begin forwarded message` and the payload sits *below* it, because forwarding a supplier's
  mail to himself is how the client works. Only 3 emails contain reply quoting at all. A
  stripper tuned for a normal mailbox would delete two thirds of the corpus.
- **`8+256` means 8GB RAM and 256GB storage.** Samsung convention, throughout.
- **`offers.identity_key` and `ProductSpec.identity_key()` describe the same rule in two
  languages.** If one changes and the other does not, reconciliation silently creates
  duplicate rows instead of updating them. Change both, or neither.

---

## Layout

```
app/
  main.py              FastAPI assembly — routing and wiring only
  pipeline.py          one email, end to end
  config.py            settings from env
  api/                 HTTP surface, no business logic
  brain/               every decision about the trade
    normalise/         models, money, colours, grades, regions, variants
    tables/            HTML and spreadsheet extraction
    llm/               dual-provider client, column mapping, usage accounting
  db/                  Repository protocol, in-memory store, Supabase implementation
  schemas/             the contracts: what n8n sends, what extraction returns
frontend/              React dashboard
supabase/migrations/   schema, in numbered order
n8n/                   the inbox workflow
demo/                  six emails that prove the system works
scripts/               sample loading, smoke checks, matching demo
samples/               real client emails — gitignored, never committed
docs/                  sample-email analysis, deploy notes
```

---

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements-dev.txt
copy .env.example .env            # fill in INGEST_TOKEN at minimum
uvicorn app.main:app --reload
```

`http://127.0.0.1:8000/health` should return `{"status":"ok",...}`; API docs are at `/docs`.

```bash
pytest                            # 608 tests
ruff check .
```

Dashboard:

```bash
cd frontend
npm install
copy .env.example .env.local      # project URL + publishable key
npm run dev
```

Two one-off steps before anything appears on the board: create a login in Supabase → 
Authentication → Users, then run `supabase/setup_tenant.sql` with that user's UID pasted in.
A user with no `profiles` row sees an empty board rather than an error — row-level security
simply returns no rows, which looks like a broken dashboard and is really a missing row.

Loading the demo:

```bash
python scripts\load_samples.py --tenant <tenant-uuid> --samples demo --wipe
```

---

Backend deploys to Render from `main`; the blueprint is `render.yaml` and secrets are set in
the Render dashboard, never in the repo.
