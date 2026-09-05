-- 0008_deals.sql
--
-- Deals: the wrapper that turns a match into work in progress.
--
-- Four rules from the specification shape this schema, and each one rules out a design
-- that would otherwise be the obvious choice.
--
-- **A deal references line items; it never relocates them.** There is no deal_id on
-- offers. The same lot may be relevant to two deals at once and remains a price-history
-- data point either way, so the link lives in its own table and the order book is
-- untouched by it.
--
-- **The numbers are captured at the time they were agreed.** Allocations carry their own
-- quantity and unit price rather than reading through to the offer. A supplier's price
-- list expires every morning; a deal struck yesterday at $905 did not.
--
-- **Many-to-many in both directions.** One buyer filled by three sellers, and one lot
-- split across four buyers, are the same shape — so one allocation table with a side,
-- not two tables.
--
-- **Status is free text and there is no stage model.** An explicit client decision: a
-- pipeline the trader does not believe in goes stale, and stale pipeline data is worse
-- than none.

create table public.deals (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,

    -- Human-facing, per tenant, so the team can say "deal 114" out loud.
    reference           integer not null,

    title               text,

    -- Free text on purpose. See above.
    status              text not null default '',
    status_updated_at   timestamptz not null default now(),
    status_updated_by   uuid references auth.users (id) on delete set null,

    -- Lead claiming, so two people do not work the same lot.
    owner_id            uuid references auth.users (id) on delete set null,

    -- On: the referenced lots stay in the Sellers list marked 'in play'. Off: hidden
    -- from the rest of the team while this deal is open. Per deal, by design — the
    -- trader decides case by case whether he is protecting a source.
    keep_offers_visible boolean not null default true,

    opened_at           timestamptz not null default now(),
    closed_at           timestamptz,
    -- 'won' | 'lost'. Null while open. Not a stage model: this is the one distinction
    -- that has to be a column because the loss reason below is only meaningful against
    -- it, and because a closed deal must leave the working list.
    outcome             text check (outcome in ('won', 'lost')),
    -- Captured at close-out, per the spec. What we quoted and why it went away is the
    -- only thing that makes a lost deal worth having recorded.
    quoted_unit_price   numeric(12, 2),
    loss_reason         text,

    created_at          timestamptz not null default now(),

    unique (tenant_id, reference)
);

create index deals_tenant_open_idx on public.deals (tenant_id, opened_at desc)
    where closed_at is null;

comment on column public.deals.status is
    'Free text. There is deliberately no stage enum — an explicit client decision, on '
    'the grounds that a pipeline the trader does not believe in goes stale, and stale '
    'pipeline data is worse than none.';


-- ---------------------------------------------------------------------------
-- Allocations — one leg of a deal
-- ---------------------------------------------------------------------------

create table public.deal_allocations (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    deal_id             uuid not null references public.deals (id) on delete cascade,

    -- The line item this leg refers to. ON DELETE SET NULL rather than CASCADE: a deal
    -- must survive the offer it came from being superseded, which happens every morning.
    offer_id            uuid references public.offers (id) on delete set null,
    counterparty_id     uuid not null references public.counterparties (id) on delete restrict,

    -- 'buy' = we are buying this leg from a seller. 'sell' = we are selling it to a
    -- buyer. Both live here because one lot across four buyers and one buyer from three
    -- sellers are the same shape.
    side                public.offer_side not null,

    -- Frozen at the moment of agreement. Deliberately not read through to the offer.
    quantity            integer not null check (quantity > 0),
    unit_price          numeric(12, 2),
    currency            char(3),

    -- Per-counterparty free text, in addition to the deal-level status.
    note                text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index deal_allocations_deal_idx  on public.deal_allocations (deal_id);
create index deal_allocations_offer_idx on public.deal_allocations (tenant_id, offer_id)
    where offer_id is not null;

comment on column public.deal_allocations.unit_price is
    'Frozen at agreement. A supplier''s list expires every morning; a deal struck '
    'yesterday at 905 did not.';


-- ---------------------------------------------------------------------------
-- Reference numbers
-- ---------------------------------------------------------------------------

-- Per tenant rather than global. Two clients both having a "deal 114" is correct; one
-- client seeing his deals jump from 3 to 4,000 because another client is busy is not.
create or replace function public.next_deal_reference(p_tenant uuid)
returns integer
language sql
security definer
set search_path = public
as $$
    select coalesce(max(reference), 0) + 1 from public.deals where tenant_id = p_tenant;
$$;


-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

alter table public.deals            enable row level security;
alter table public.deal_allocations enable row level security;

create policy deals_tenant on public.deals
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy deal_allocations_tenant on public.deal_allocations
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());


-- ---------------------------------------------------------------------------
-- In play
-- ---------------------------------------------------------------------------

-- `offers.in_conversation` has existed since 0002 and nothing has ever set it. This is
-- what it is for: a lot referenced by an open deal is marked so the rest of the team can
-- see it is being worked, without it leaving the order book.
create or replace function public.refresh_offer_in_play()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    target uuid := coalesce(new.offer_id, old.offer_id);
begin
    if target is null then
        return coalesce(new, old);
    end if;

    update public.offers o
       set in_conversation = exists (
               select 1
                 from public.deal_allocations a
                 join public.deals d on d.id = a.deal_id
                where a.offer_id = target
                  and d.closed_at is null
                  and d.keep_offers_visible
           )
     where o.id = target;

    return coalesce(new, old);
end $$;

create trigger deal_allocations_mark_in_play
    after insert or update or delete on public.deal_allocations
    for each row execute function public.refresh_offer_in_play();
