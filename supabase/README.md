# Database

Postgres on Supabase. Multi-tenant, isolated with row-level security.

## Applying the schema

Run the migrations **in numbered order** in the Supabase SQL editor. Each file is
independent but later ones depend on types and functions defined earlier, so order
matters.

| File | What it creates |
|---|---|
| `0001_tenancy.sql` | tenants, profiles, `app.current_tenant_id()`, `app.is_platform_owner()`, RLS baseline |
| `0002_trading_core.sql` | counterparties, emails, imports, offers, offer_changes |
| `0003_reference_data.sql` | grades and per-sender aliases, model catalogue, freight rates |
| `0004_outbound.sql` | templates, outbound_messages, conversations |
| `0005_usage.sql` | token and cost accounting |

Then run `tests/rls_isolation.sql` and do not build anything on top until it prints
`RLS ISOLATION PASSED`.

## The three rules the schema encodes

**Isolation lives in the database, not the API.** Every tenant-scoped table has RLS on
and a policy comparing `tenant_id` to the signed-in user's tenant. If isolation were
enforced in application code, one forgotten `WHERE` clause would leak a broker's
supplier prices to a competitor. Here, a forgotten `WHERE` returns nothing instead.

The backend connects with the **secret key** (`sb_secret_…`, formerly called the
service role key), **which bypasses RLS entirely**. That is intended — it has to write
for every tenant. It is also exactly why that key lives only in `.env` and Render's
environment, and never reaches a browser. The **publishable key** (`sb_publishable_…`)
is the one the React dashboard uses, and it is safe there only because RLS is on.

**Identity excludes price and quantity.** `offers.identity_key` is a generated column
over model, storage, colour, grade and region code. The same lot at a new price is the
same offer updated, not a new row. `offers_live_identity_uniq` enforces one live offer
per identity per counterparty, and exempts closed rows so a lot can legitimately
reappear after being marked sold.

**Insertions are harmless, closures are destructive.** A wrong new row is noise;
wrongly marking a live 120-unit lot sold loses a deal. So `imports` records the row
count of every list against that sender's usual count, `offer_changes` records the old
value, new value and cause of every mutation, and `emails` and `imports` are read-only
to clients — evidence a user can edit is not evidence.

## Keeping SQL and Python in step

`offers.identity_key` and `ExtractedLineItem.identity_key()` in
`app/schemas/offer.py` describe the same rule in two languages. If one changes and the
other does not, reconciliation silently starts creating duplicate rows instead of
updating them. Change both, or neither.
