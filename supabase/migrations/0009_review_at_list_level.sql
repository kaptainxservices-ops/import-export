-- 0009_review_at_list_level.sql
--
-- Review that scales to a 500-row price list.
--
-- The spec is emphatic and the reasoning is worth restating, because it is what shapes
-- every column below: *nobody reviews 500 rows*. Confident rows go live immediately and
-- only the uncertain ones queue, so the summary — "487 parsed, 12 flagged" — is doing the
-- real work. Twelve of 487 is a normal morning: approve and fix the twelve. Three hundred
-- and forty of 392 means the sender changed their template and the columns misaligned,
-- and the right action is to reject the whole import in one click rather than correct 340
-- rows by hand.
--
-- Scoring the 45 real sample emails settled the shape of this. A first attempt flagged
-- 37% of all rows; separating "how much did we infer" from "could a person fix this row"
-- brought it to 1.3%, which is the ratio the spec describes. Both halves are stored:
-- `confidence` and `review_reason` are the row's own, `parse_warnings` on the import is
-- everything true of the list rather than of any one line.

alter table public.offers
    -- Held out of the live order book until a human resolves it.
    add column if not exists needs_review  boolean not null default false,
    add column if not exists review_reason text,
    -- Per-field scores, so a correction can see which field was weak rather than being
    -- told the row as a whole was doubtful.
    add column if not exists confidence_json jsonb;

-- Flagged rows are the working set of the review screen and are a tiny fraction of the
-- table, so the index carries only them.
create index if not exists offers_needs_review_idx
    on public.offers (tenant_id, created_at desc) where needs_review;

comment on column public.offers.needs_review is
    'Held out of the live board for a person. Deliberately not the same as low '
    'confidence: a missing currency scores badly and is fixed once on the supplier, not '
    'a thousand times on the rows.';


alter table public.imports
    add column if not exists flagged_rows   integer not null default 0,
    -- Everything true of the list rather than of one row: no currency anywhere, product
    -- names matching nothing in the catalogue. Raised once, fixed once.
    add column if not exists parse_warnings text[] not null default '{}',
    -- Null while waiting. The two ways an import leaves the queue.
    add column if not exists approved_at    timestamptz,
    add column if not exists rejected_at    timestamptz,
    add column if not exists reviewed_by    uuid references auth.users (id) on delete set null,
    -- Which import this one retired, so "what changed" is a join rather than a guess.
    add column if not exists supersedes_id  uuid references public.imports (id) on delete set null;

create index if not exists imports_waiting_idx
    on public.imports (tenant_id, created_at desc)
    where approved_at is null and rejected_at is null;

comment on column public.imports.parse_warnings is
    'True of the list, not of any one row. Presenting a missing currency against each of '
    '1,103 rows is presenting it in the wrong place.';
