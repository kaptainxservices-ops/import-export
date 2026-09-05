# Dashboard

React + Vite + TypeScript. Talks to Supabase directly — there is no API layer between
the browser and the database, because there does not need to be one.

## Why it is safe to query the database from the browser

The dashboard uses the **publishable** key. What a signed-in user can read is decided by
row-level security in Postgres, not by this code, so a bug here cannot leak another
tenant's prices. The **secret** key must never appear in this folder: Vite inlines
anything prefixed `VITE_` into the bundle, where anyone who opens the page can read it,
and the secret key bypasses every policy. `src/lib/supabase.ts` refuses to start if it
is given one.

## Running it

```bash
cd frontend
npm install
copy .env.example .env.local     # then fill in the two values
npm run dev
```

Both values come from **Supabase → Project Settings → API**: the project URL, and the
key labelled **publishable** (`sb_publishable_…`), not the secret one.

## Before anything appears on the board

Two things, both one-off:

1. **Create a login** — Supabase → Authentication → Users → Add user.
2. **Run `supabase/setup_tenant.sql`** with that user's UID pasted in. It creates the
   tenant, links the login to it, and enables realtime on `offers`.

A user with no `profiles` row sees an empty board rather than an error, because
row-level security simply returns no rows. That looks like a broken dashboard and is
really a missing row — which is why the setup script exists.

## What is here

| | |
|---|---|
| **Sellers / Buyers** | the two sides of the board, filterable and sortable |
| **Filters** | search across description, model and EAN; brand; category; live only |
| **Freshness** | today / 1–3 days / stale, per row |
| **Source drawer** | the original email behind any row |
| **Realtime** | the board updates itself when the backend writes |

Filtering runs in Postgres rather than the browser. A busy morning puts thousands of
rows on the board, and shipping all of them to filter locally is slow on the trader's
laptop and wasteful of the free tier.

## The source drawer is the point

Every number on the board was read out of an email by software. The first time a price
looks wrong, the trader will want to see what the supplier actually wrote — and if he
cannot, he stops believing the board. A board he does not believe is worse than no
board, because he then checks everything twice.

The drawer shows `body_text`: the message *as parsed*, with quoted history and
signatures already stripped. That is deliberate — it is evidence of what the extractor
actually read, not just what arrived.

## Not built yet

Matches screen (the engine exists in `app/brain/matching.py`), templates and outbound
email, the review queue for emails whose sender could not be established, and the admin
panel.
