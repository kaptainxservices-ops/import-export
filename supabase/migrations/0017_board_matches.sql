-- 0017: the match board, worked out once and kept
--
-- /matches/board ran the whole matching engine on every request: every live
-- requirement against every live lot, ~3,400 rows fetched a page at a time, on every
-- visit, every filter change, every keystroke of a search. The board only changes when
-- a list arrives -- twenty or thirty times a day -- so almost all of that work produced
-- the answer it produced last time.
--
-- This keeps the answer. One row per live buyer requirement, written by the backend
-- (app/board_store.py) after anything changes an offer, and read by the dashboard
-- straight from Postgres like Sellers and Buyers. A change recomputes only the
-- requirements it can touch: the ones the matching engine itself relates to a changed
-- lot, and the ones whose stored options used it.
--
-- Written only by the backend with the service role; clients read their own tenant's
-- rows and never write them. A board a user could edit would stop being an answer.
--
-- Safe to run more than once.

create table if not exists public.board_matches (
    offer_id            uuid primary key references public.offers (id) on delete cascade,
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    buyer_id            uuid references public.counterparties (id) on delete set null,

    -- The requirement, as the row shows it.
    description         text not null default '',
    spec_parts          text[] not null default '{}',
    counterparty_name   text,
    country             text,
    quantity            integer,
    unit_price          numeric(14, 4),
    currency            text,
    brand               text,
    category            text,
    -- Lower-cased description, model, EAN, brand, capacity, colour and buyer, for the
    -- product search: every word typed must appear in it.
    search_text         text not null default '',

    -- What can fill it.
    state               text not null
        check (state in ('filled', 'short', 'near_miss', 'no_supply')),
    state_rank          smallint not null,
    option_count        integer not null default 0,
    near_miss_count     integer not null default 0,
    best_summary        text,
    best_legs           jsonb not null default '[]',
    best_currency       text,
    blended_unit_cost   numeric(14, 4),
    total_margin        numeric(16, 4),
    margin_pct          numeric(9, 4),
    shortfall           integer not null default 0,
    filled_quantity     integer not null default 0,
    note                text,
    -- The real options, best first, each with its suppliers and legs: what the client
    -- filter uses to show "the best option this supplier is part of".
    options             jsonb not null default '[]',
    -- Every counterparty in any option, real or near miss. The supplier filter.
    supplier_ids        uuid[] not null default '{}',
    -- Every lot in any option. When one of these changes, this row is recomputed even
    -- if the lot no longer resembles the requirement at all.
    offer_ids           uuid[] not null default '{}',

    -- When the best option became possible: the later of the requirement's and its
    -- newest lot's first_seen_at. A supplier resending yesterday's list does not move it.
    matched_at          timestamptz,
    -- When this system first saw the requirement fillable. Set once, never cleared:
    -- it is what the alerts announce, and a requirement is announced once.
    first_matched_at    timestamptz,
    last_confirmed_at   timestamptz,
    -- A hash of everything above except the timestamps, so an unchanged row is not
    -- rewritten (and does not fire a realtime event) when a refresh recomputes it.
    content_hash        text,
    computed_at         timestamptz not null default now()
);

create index if not exists board_matches_tenant_idx
    on public.board_matches (tenant_id, state_rank);
create index if not exists board_matches_first_matched_idx
    on public.board_matches (tenant_id, first_matched_at desc)
    where first_matched_at is not null;
create index if not exists board_matches_supplier_idx
    on public.board_matches using gin (supplier_ids);

comment on table public.board_matches is
    'One row per live buyer requirement with its best fill, kept current by '
    'app/board_store.py. Read by the dashboard; written only by the backend.';


-- One row per tenant: how far the stored board has caught up.
create table if not exists public.board_match_state (
    tenant_id               uuid primary key references public.tenants (id) on delete cascade,
    -- The newest offers.updated_at already reflected in board_matches.
    offers_watermark        timestamptz,
    counterparties_watermark timestamptz,
    -- The model equivalences the board was computed with. When they change, every
    -- requirement is recomputed: an alias can join any two products.
    alias_fingerprint       text,
    supply_rows             integer not null default 0,
    last_full_at            timestamptz,
    last_refresh_at         timestamptz,
    updated_at              timestamptz not null default now()
);


-- ---------------------------------------------------------------- access

alter table public.board_matches     enable row level security;
alter table public.board_match_state enable row level security;

drop policy if exists board_matches_read on public.board_matches;
create policy board_matches_read on public.board_matches
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());

drop policy if exists board_match_state_read on public.board_match_state;
create policy board_match_state_read on public.board_match_state
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());


-- ---------------------------------------------------------------- realtime
-- The dashboard updates the moment a row changes, and the match alerts are these
-- events. Without this the board only refreshes on reload.
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and tablename = 'board_matches'
    ) then
        alter publication supabase_realtime add table public.board_matches;
    end if;
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and tablename = 'board_match_state'
    ) then
        alter publication supabase_realtime add table public.board_match_state;
    end if;
end $$;
