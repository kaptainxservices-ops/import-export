-- 0002_trading_core.sql
-- Counterparties, emails, imports, offers and offer history.
--
-- Three design decisions are load-bearing here.
--
-- 1. The stored unit is the LINE ITEM, never the email. One message may carry a single
--    lot or a 500-row catalogue.
--
-- 2. Offer identity excludes price and quantity (see the identity_key generated
--    column). Those two are exactly what change day to day; including them would make
--    every reprice look like a new offer and break daily reconciliation.
--
-- 3. Nothing is ever deleted. Offers close, they do not disappear, and every change
--    writes a history row. The client has to be able to ask "why did this say sold?"
--    and get an answer.


-- ---------------------------------------------------------------------------
-- Counterparties — who we trade with
-- ---------------------------------------------------------------------------
-- Deliberately thin. This is a sender identity so offers have an owner, not a CRM.

create table public.counterparties (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,

    name                text,
    primary_email       text not null,
    other_emails        text[] not null default '{}',
    country             text,

    -- Overrides the tenant default when this seller's stock moves at a different pace.
    staleness_hours     integer check (staleness_hours between 1 and 720),

    -- Learned from history: how many rows this sender's list usually has. Powers the
    -- row-count sanity check that stops a bad parse from closing live stock.
    typical_row_count   integer,

    notes               text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    unique (tenant_id, primary_email)
);

create index counterparties_tenant_idx on public.counterparties (tenant_id);


-- ---------------------------------------------------------------------------
-- Emails — the source of truth behind every number on screen
-- ---------------------------------------------------------------------------

create type public.email_direction    as enum ('inbound', 'outbound');
create type public.email_classification as enum (
    'seller_offer', 'buyer_request', 'both', 'negotiation', 'irrelevant', 'unclassified'
);

create table public.emails (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    counterparty_id     uuid references public.counterparties (id) on delete set null,

    message_id          text not null,
    in_reply_to         text,
    thread_id           text,
    direction           public.email_direction not null default 'inbound',

    from_email          text not null,
    from_name           text,
    to_emails           text[] not null default '{}',
    subject             text,
    received_at         timestamptz not null,

    -- What was actually parsed: quoted history and signature already stripped.
    body_text           text,
    -- The complete original, kept only to render the source drawer. Never parsed.
    body_raw            text,
    attachments         jsonb not null default '[]'::jsonb,

    classification      public.email_classification not null default 'unclassified',
    processed_at        timestamptz,
    error               text,

    created_at          timestamptz not null default now(),

    -- Retries and mailbox re-syncs will redeliver the same message. Dedupe in the
    -- database rather than hoping n8n never sends twice.
    unique (tenant_id, message_id)
);

create index emails_tenant_received_idx on public.emails (tenant_id, received_at desc);
create index emails_thread_idx          on public.emails (tenant_id, thread_id);
create index emails_in_reply_to_idx     on public.emails (tenant_id, in_reply_to);


-- ---------------------------------------------------------------------------
-- Imports — one row per list processed
-- ---------------------------------------------------------------------------
-- This table exists for one reason: to make the disappearance rule safe.
--
-- Once absence is read as "sold", a failed parse becomes destructive. If a sender's
-- list normally yields 487 rows and today's malformed attachment yields 12,
-- reconciliation would close 475 live offers. So every import records what it saw,
-- and closure is refused when the count collapses.

create type public.import_status as enum (
    'applied',
    'flagged_low_row_count',   -- suspiciously small; nothing was closed
    'flagged_partial_list',    -- looks like "just got 50 more", not a full list
    'failed'
);

create table public.imports (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    counterparty_id     uuid references public.counterparties (id) on delete set null,
    email_id            uuid references public.emails (id) on delete set null,

    row_count           integer not null default 0,
    previous_row_count  integer,
    is_complete_list    boolean,          -- NULL = undecided, which must close nothing

    status              public.import_status not null default 'applied',
    offers_inserted     integer not null default 0,
    offers_updated      integer not null default 0,
    offers_refreshed    integer not null default 0,
    offers_closed       integer not null default 0,

    notes               text,
    created_at          timestamptz not null default now()
);

create index imports_tenant_created_idx on public.imports (tenant_id, created_at desc);
create index imports_flagged_idx        on public.imports (tenant_id, status)
    where status <> 'applied';


-- ---------------------------------------------------------------------------
-- Offers — the line items
-- ---------------------------------------------------------------------------

create type public.offer_side   as enum ('sell', 'buy');
create type public.offer_status as enum ('live', 'sold', 'withdrawn', 'expired');
create type public.price_basis  as enum ('per_unit', 'per_lot', 'unknown');

create table public.offers (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    counterparty_id     uuid not null references public.counterparties (id) on delete cascade,
    source_email_id     uuid references public.emails (id) on delete set null,
    source_ref          text,             -- 'line 14' or 'Sheet1!C7', for the drawer

    side                public.offer_side not null,

    -- Canonical spec. These six plus side form the identity.
    model               text,
    storage_gb          integer,
    colour              text,
    grade               text,
    region_code         text,

    -- Commercials. These change; that is the point of keeping them out of identity.
    quantity            integer,
    unit_price          numeric(12, 2),
    currency            char(3),
    price_basis         public.price_basis not null default 'unknown',
    incoterm            text,
    vat_included        boolean,
    location            text,

    -- Exactly what the sender wrote, before normalisation. When the client says a
    -- grade is wrong, this is what settles it — and it lets normalisation rules be
    -- re-run over old rows without having lost the original.
    raw                 jsonb not null default '{}'::jsonb,
    confidence          numeric(3, 2) not null default 1.00
                        check (confidence between 0 and 1),

    status              public.offer_status not null default 'live',
    close_reason        text,
    in_conversation     boolean not null default false,

    first_seen_at       timestamptz not null default now(),
    last_confirmed_at   timestamptz not null default now(),
    closed_at           timestamptz,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    -- Mirrors ExtractedLineItem.identity_key() in app/schemas/offer.py.
    -- Keep the two in step: if one changes and the other does not, reconciliation
    -- starts creating duplicates instead of updating rows.
    --
    -- `side` is deliberately NOT in this expression even though it is part of identity.
    -- Casting an enum to text is only STABLE, not IMMUTABLE, and Postgres refuses
    -- non-immutable expressions in a stored generated column. It is carried in the
    -- unique index below instead, which is equivalent.
    identity_key        text generated always as (
        lower(coalesce(model, ''))      || '|' ||
        coalesce(storage_gb::text, '')  || '|' ||
        lower(coalesce(colour, ''))     || '|' ||
        lower(coalesce(grade, ''))      || '|' ||
        lower(coalesce(region_code, ''))
    ) stored,

    constraint closed_offers_have_a_reason check (
        status = 'live' or close_reason is not null
    )
);

-- One live offer per identity per counterparty. Closed rows are exempt, so the same
-- lot can legitimately reappear later as a new row after being marked sold.
create unique index offers_live_identity_uniq
    on public.offers (tenant_id, counterparty_id, side, identity_key)
    where status = 'live';

create index offers_board_idx      on public.offers (tenant_id, side, status, last_confirmed_at desc);
create index offers_model_idx      on public.offers (tenant_id, model, storage_gb) where status = 'live';
create index offers_matching_idx   on public.offers (tenant_id, side, model, storage_gb, grade, region_code)
    where status = 'live';
create index offers_counterparty_idx on public.offers (counterparty_id);

create trigger offers_touch
    before update on public.offers
    for each row execute function app.touch_updated_at();

create trigger counterparties_touch
    before update on public.counterparties
    for each row execute function app.touch_updated_at();


-- ---------------------------------------------------------------------------
-- Offer changes — every mutation, with its cause
-- ---------------------------------------------------------------------------
-- Insertions are harmless; updates and closures are destructive. A wrong new row is
-- noise, but wrongly marking a live 120-unit lot sold loses a deal. So every change
-- records the old value, the new value and what caused it, and stays reversible.

create table public.offer_changes (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    offer_id            uuid not null references public.offers (id) on delete cascade,

    field               text not null,
    old_value           text,
    new_value           text,

    cause               text not null,    -- 'daily_list', 'reply', 'manual', 'expiry'
    caused_by_email_id  uuid references public.emails (id) on delete set null,
    caused_by_user_id   uuid references public.profiles (id) on delete set null,
    import_id           uuid references public.imports (id) on delete set null,

    created_at          timestamptz not null default now()
);

create index offer_changes_offer_idx  on public.offer_changes (offer_id, created_at desc);
create index offer_changes_tenant_idx on public.offer_changes (tenant_id, created_at desc);


-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
-- Same shape on every table: the platform owner sees everything, a client sees only
-- its own tenant. The service role key used by the backend bypasses RLS entirely,
-- which is precisely why that key never leaves Render.

alter table public.counterparties enable row level security;
alter table public.emails         enable row level security;
alter table public.imports        enable row level security;
alter table public.offers         enable row level security;
alter table public.offer_changes  enable row level security;

create policy counterparties_tenant on public.counterparties
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

-- Emails and imports are read-only to clients. Both are evidence: the source drawer
-- is the trust anchor for every number on the board, and the import log is what
-- explains why an offer was closed. Evidence a user can edit stops being evidence.
-- The backend writes them with the service role, which bypasses RLS.
create policy emails_read on public.emails
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy imports_read on public.imports
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy offers_tenant on public.offers
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

-- History is readable but never editable from the client. An audit trail that the
-- audited party can rewrite is not an audit trail.
create policy offer_changes_read on public.offer_changes
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());
