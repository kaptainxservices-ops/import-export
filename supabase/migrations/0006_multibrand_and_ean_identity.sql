-- 0006_multibrand_and_ean_identity.sql
--
-- Rework driven by the real sample emails (see docs/sample-email-analysis.md).
-- The original schema assumed a secondary iPhone trade. The 45 sample emails describe
-- a general electronics wholesale brokerage: Apple, Samsung, Google, Xiaomi, Motorola,
-- Sony and Nintendo, plus televisions, consoles, power banks, cases and — in one list —
-- hand sanitiser. Columns shaped for a phone leave most of that unrepresentable.
--
-- Three changes:
--
--   1. A generic product core (brand, category, EAN, description) with the
--      phone-specific attributes demoted to optional.
--   2. Identity keyed on EAN when the supplier provides one. Roughly half the sample
--      volume carries an EAN, which is exact, global and needs no interpretation.
--   3. Per-counterparty parsing settings — currency and decimal convention — because
--      '$' and '1,079' cannot be resolved from the text alone.
--
-- IDEMPOTENT. Every statement checks the current state first, so this can be re-run
-- safely after a partial application. Supabase's SQL editor stops at the first error
-- and leaves earlier statements applied, which makes a run-once migration a trap: the
-- second attempt fails on work that already succeeded, and the real problem stays
-- hidden behind a misleading error.


-- ---------------------------------------------------------------------------
-- Offers: generic product core
-- ---------------------------------------------------------------------------

-- The old identity expression and its index must go before the columns they
-- reference change.
drop index if exists public.offers_live_identity_uniq;
alter table public.offers drop column if exists identity_key;

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'offers' and column_name = 'storage_gb'
    ) and not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'offers' and column_name = 'capacity_gb'
    ) then
        alter table public.offers rename column storage_gb to capacity_gb;
    end if;
end $$;

alter table public.offers
    add column if not exists brand           text,
    add column if not exists category        text,
    add column if not exists ean             text,
    add column if not exists description     text,
    add column if not exists description_key text,
    add column if not exists ram_gb          integer,
    add column if not exists network         text,
    add column if not exists dual_sim        boolean,
    add column if not exists edition         text,
    -- Anything that does not fit a column: screen size, wattage, part codes. Kept as
    -- data rather than as new columns for every category that turns up.
    add column if not exists attributes      jsonb not null default '{}'::jsonb;

comment on column public.offers.ean is
    'GTIN-8 or GTIN-13. When present this alone identifies the product globally.';
comment on column public.offers.description is
    'The supplier''s own wording, kept verbatim for the source drawer.';
comment on column public.offers.description_key is
    'Normalised description used for identity when no EAN exists. Computed in '
    'app/brain/normalise/product.py — the two must stay in step.';
comment on column public.offers.model is
    'Canonical model name where one could be parsed. Null for accessories and sundries.';


-- ---------------------------------------------------------------------------
-- Identity: EAN first, spec fallback
-- ---------------------------------------------------------------------------
-- Mirrors ProductSpec.identity_key(). If one changes and the other does not,
-- reconciliation stops updating rows and starts creating duplicates.
--
-- Colour is in the fallback deliberately. Suppliers who give colour its own column
-- never repeat it in the description; without it, every finish of a product collapses
-- into one identity. On one real 1,654-row list that merged 27% of the rows.

alter table public.offers
    add column if not exists identity_key text generated always as (
        case
            when ean is not null and ean <> ''
                then 'ean:' || ean
            else 'spec:'
                 || lower(coalesce(brand, ''))           || '|'
                 || lower(coalesce(description_key, '')) || '|'
                 || coalesce(capacity_gb::text, '')      || '|'
                 || lower(coalesce(colour, ''))
        end
    ) stored;

-- One live offer per identity per counterparty. Closed rows are exempt, so a lot can
-- legitimately reappear after being marked sold.
create unique index if not exists offers_live_identity_uniq
    on public.offers (tenant_id, counterparty_id, side, identity_key)
    where status = 'live';

create index if not exists offers_ean_idx
    on public.offers (tenant_id, ean) where ean is not null;

create index if not exists offers_brand_idx
    on public.offers (tenant_id, brand, category) where status = 'live';

-- Cross-supplier matching by EAN is an equality test rather than a normalisation
-- problem. This index is what makes combination fills cheap.
create index if not exists offers_ean_match_idx
    on public.offers (tenant_id, ean, side)
    where status = 'live' and ean is not null;


-- ---------------------------------------------------------------------------
-- Counterparties: per-supplier parsing settings
-- ---------------------------------------------------------------------------

alter table public.counterparties
    add column if not exists default_currency  char(3),
    add column if not exists decimal_separator text,
    add column if not exists typical_language  text;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'counterparties_decimal_separator_check'
    ) then
        alter table public.counterparties
            add constraint counterparties_decimal_separator_check
            check (decimal_separator in ('comma', 'dot'));
    end if;
end $$;

comment on column public.counterparties.default_currency is
    'Resolves a bare $ or an unmarked number. Never guessed: a currency error does not '
    'present as an error, it presents as an unusually good margin.';
comment on column public.counterparties.decimal_separator is
    'Resolves genuinely ambiguous values such as 1,079 — which is 1079 or 1.079 with '
    'nothing in the string to distinguish them.';


-- ---------------------------------------------------------------------------
-- Tenants: which addresses count as internal
-- ---------------------------------------------------------------------------
-- Needed to tell a supplier's email from one the client's own staff sent. Cannot be
-- detected, so it is configuration.

alter table public.tenants
    add column if not exists internal_domains   text[] not null default '{}',
    add column if not exists internal_addresses text[] not null default '{}';


-- ---------------------------------------------------------------------------
-- Emails: how the sender was established
-- ---------------------------------------------------------------------------
-- Most mail arrives directly from suppliers. Some is relayed by hand — offers that
-- came in on WhatsApp, retyped by staff into a fresh email — and those carry no
-- supplier address at all. Those must never be attributed silently: a wrong
-- attribution corrupts two suppliers' boards at once, one gaining rows it never sent
-- and the other having live stock closed as sold.

do $$
begin
    if not exists (select 1 from pg_type where typname = 'sender_method') then
        create type public.sender_method as enum (
            'envelope',          -- direct from the supplier; the normal case
            'forwarded_header',  -- read out of a 'Begin forwarded message' block
            'body_signature',    -- guessed from an address in the body — needs review
            'subject_company',   -- only a company name in the subject — needs review
            'unresolved',
            'manual'             -- a human attributed it
        );
    end if;
end $$;

alter table public.emails
    add column if not exists counterparty_method     public.sender_method not null
        default 'envelope',
    add column if not exists counterparty_confidence numeric(3, 2) not null default 1.00,
    add column if not exists needs_sender_review     boolean not null default false,
    add column if not exists suggested_sender_name   text;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'emails_counterparty_confidence_check'
    ) then
        alter table public.emails
            add constraint emails_counterparty_confidence_check
            check (counterparty_confidence between 0 and 1);
    end if;
end $$;

create index if not exists emails_needs_sender_review_idx
    on public.emails (tenant_id, received_at desc)
    where needs_sender_review;


-- ---------------------------------------------------------------------------
-- Imports: unparseable content, and volatile row counts
-- ---------------------------------------------------------------------------
-- A SharePoint link behind a login, or a photograph of a price list, cannot be
-- extracted. Those must surface rather than vanish, or the client quietly loses offers
-- and never learns why.

alter type public.import_status add value if not exists 'flagged_unparseable';

alter table public.imports
    add column if not exists rows_expanded_from_variants integer not null default 0,
    add column if not exists duplicate_rows              integer not null default 0;

comment on column public.imports.rows_expanded_from_variants is
    'How many rows came from expanding a multi-colour line such as '
    '"Black / Blue / Grey - EUR 125". Row counts are volatile because of this, and the '
    'row-count safety check has to account for it.';
comment on column public.imports.duplicate_rows is
    'Rows dropped because the same identity appeared twice in one list — the same '
    'product under two section headings, or once with an EAN and once without. Two '
    'rows describing one lot are not two lots.';
