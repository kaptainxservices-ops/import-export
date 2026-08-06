-- 0005_usage.sql
-- Token and cost accounting per tenant.
--
-- Anthropic is the only cost that scales linearly with clients, so it is the only one
-- worth measuring per tenant. Recording it from the first call means the answer to
-- "is this client profitable?" is a query rather than a guess, and a runaway retry
-- loop shows up as a spike instead of as a surprise on the monthly bill.

create table public.usage_events (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    email_id            uuid references public.emails (id) on delete set null,

    operation           text not null,          -- 'classify' | 'extract' | 'columns' | 'personalise'
    model               text not null,
    input_tokens        integer not null default 0,
    output_tokens       integer not null default 0,
    cost_usd            numeric(10, 6) not null default 0,

    duration_ms         integer,
    succeeded           boolean not null default true,
    error               text,

    created_at          timestamptz not null default now()
);

create index usage_tenant_created_idx on public.usage_events (tenant_id, created_at desc);
create index usage_failures_idx       on public.usage_events (tenant_id, created_at desc)
    where not succeeded;

alter table public.usage_events enable row level security;

-- A client may see its own consumption; only the backend (service role) writes.
create policy usage_read on public.usage_events
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());
