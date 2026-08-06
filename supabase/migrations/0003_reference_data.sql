-- 0003_reference_data.sql
-- The vocabulary and rates matching depends on.
--
-- Normalising grades is the hardest part of extraction, because grade language is not
-- standard across the trade. "A" from one supplier is not "A" from another, and the
-- client is the only authority on what his suppliers mean. So the mapping is data he
-- owns and edits, not constants buried in a prompt.


-- ---------------------------------------------------------------------------
-- Grade scale — per tenant, ranked
-- ---------------------------------------------------------------------------
-- rank orders the scale, best first (rank 1 is the best grade). Matching needs the
-- ORDER, not just the names: without it there is no way to decide whether an A+ lot
-- satisfies a request for A. It does; the reverse does not.

create table public.grades (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,

    code                text not null,          -- canonical: 'A+', 'A', 'B', 'CPO'
    rank                integer not null,       -- 1 = best
    description         text,

    created_at          timestamptz not null default now(),

    unique (tenant_id, code),

    -- Deferred: reordering a scale swaps two ranks, and a non-deferred constraint
    -- would reject the first UPDATE of the pair before the second could run.
    unique (tenant_id, rank) deferrable initially deferred
);

-- How each sender's wording maps onto the canonical scale. Per-counterparty, because
-- the same word genuinely means different things from different senders.
create table public.grade_aliases (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    counterparty_id     uuid references public.counterparties (id) on delete cascade,

    alias               text not null,          -- what they wrote: 'A grade', 'AA', '14 day'
    grade_id            uuid not null references public.grades (id) on delete cascade,

    created_at          timestamptz not null default now()
);

-- A NULL counterparty_id is the tenant-wide fallback. Postgres treats NULLs as
-- distinct in a plain unique constraint, so the fallback needs its own index.
create unique index grade_aliases_per_sender_uniq
    on public.grade_aliases (tenant_id, counterparty_id, lower(alias))
    where counterparty_id is not null;

create unique index grade_aliases_fallback_uniq
    on public.grade_aliases (tenant_id, lower(alias))
    where counterparty_id is null;


-- ---------------------------------------------------------------------------
-- Model catalogue
-- ---------------------------------------------------------------------------
-- Shared across tenants: "15PM", "15 Pro Max" and "iPhone15ProMax" mean the same
-- thing for everyone. Only grade language is client-specific.

create table public.models (
    id                  uuid primary key default gen_random_uuid(),
    canonical_name      text not null unique,   -- 'iPhone 15 Pro Max'
    family              text,                   -- 'iPhone'
    released_at         date,
    created_at          timestamptz not null default now()
);

create table public.model_aliases (
    id                  uuid primary key default gen_random_uuid(),
    model_id            uuid not null references public.models (id) on delete cascade,
    alias               text not null
);

create unique index model_aliases_uniq on public.model_aliases (lower(alias));


-- ---------------------------------------------------------------------------
-- Freight and duty
-- ---------------------------------------------------------------------------
-- Maintained by the client. Where a route has rates, Matches shows landed margin.
-- Where it does not, it shows the raw spread clearly labelled as such — a confidently
-- wrong margin is worse than an honest gap, because he would act on it.

create table public.freight_rates (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,

    origin_country      text not null,
    destination_country text not null,
    per_unit_cost       numeric(10, 2) not null,
    currency            char(3) not null default 'USD',
    duty_percent        numeric(5, 2) not null default 0,
    notes               text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    unique (tenant_id, origin_country, destination_country)
);

create trigger freight_rates_touch
    before update on public.freight_rates
    for each row execute function app.touch_updated_at();


-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table public.grades        enable row level security;
alter table public.grade_aliases enable row level security;
alter table public.freight_rates enable row level security;
alter table public.models        enable row level security;
alter table public.model_aliases enable row level security;

create policy grades_tenant on public.grades
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy grade_aliases_tenant on public.grade_aliases
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy freight_rates_tenant on public.freight_rates
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

-- The catalogue is shared reference data: readable by any signed-in user,
-- writable only by the platform owner.
create policy models_read on public.models
    for select using (auth.uid() is not null);
create policy models_owner_writes on public.models
    for all using (app.is_platform_owner()) with check (app.is_platform_owner());

create policy model_aliases_read on public.model_aliases
    for select using (auth.uid() is not null);
create policy model_aliases_owner_writes on public.model_aliases
    for all using (app.is_platform_owner()) with check (app.is_platform_owner());
