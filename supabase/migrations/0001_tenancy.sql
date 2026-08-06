-- 0001_tenancy.sql
-- Tenancy and access control. Everything else depends on this file, so it runs first.
--
-- The whole product is multi-tenant: one client per tenant, and Hardik as platform
-- owner above them. Isolation is enforced in the database with row-level security,
-- not in application code. If it were enforced in the API, one forgotten WHERE clause
-- would leak one broker's supplier prices to another. In Postgres, a forgotten WHERE
-- clause returns nothing instead.

create extension if not exists "pgcrypto";

create schema if not exists app;


-- ---------------------------------------------------------------------------
-- Tenants
-- ---------------------------------------------------------------------------

create table public.tenants (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null,
    is_active           boolean not null default true,

    -- Operational config, set by the platform owner in the admin panel.
    inbox_address       text,
    monthly_fee         numeric(10, 2),
    staleness_hours     integer not null default 24
                        check (staleness_hours between 1 and 720),

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

comment on column public.tenants.staleness_hours is
    'Default offer lifetime. Per-seller overrides live on counterparties.';


-- ---------------------------------------------------------------------------
-- Profiles — one row per login, mirroring auth.users
-- ---------------------------------------------------------------------------

create type public.user_role as enum ('platform_owner', 'client_admin', 'client_user');

create table public.profiles (
    id                  uuid primary key references auth.users (id) on delete cascade,
    tenant_id           uuid references public.tenants (id) on delete cascade,
    role                public.user_role not null default 'client_user',
    full_name           text,
    email               text,
    created_at          timestamptz not null default now(),

    -- The platform owner sits above every tenant and so has no tenant_id.
    -- Everyone else must have exactly one.
    constraint profile_tenant_matches_role check (
        (role = 'platform_owner' and tenant_id is null)
        or (role <> 'platform_owner' and tenant_id is not null)
    )
);

create index profiles_tenant_idx on public.profiles (tenant_id);


-- ---------------------------------------------------------------------------
-- Helper functions
-- ---------------------------------------------------------------------------
-- These are SECURITY DEFINER on purpose. A policy on `profiles` that queries
-- `profiles` would recurse forever; running as definer skips RLS on that lookup and
-- breaks the cycle. search_path is pinned so the functions cannot be hijacked by a
-- caller-controlled schema.

create or replace function app.current_tenant_id()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select tenant_id from public.profiles where id = auth.uid();
$$;

create or replace function app.is_platform_owner()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select coalesce(
        (select role = 'platform_owner' from public.profiles where id = auth.uid()),
        false
    );
$$;

comment on function app.current_tenant_id is
    'The tenant of the signed-in user. NULL for the platform owner and for anon.';


-- ---------------------------------------------------------------------------
-- updated_at trigger, reused by later migrations
-- ---------------------------------------------------------------------------

create or replace function app.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger tenants_touch
    before update on public.tenants
    for each row execute function app.touch_updated_at();


-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

alter table public.tenants  enable row level security;
alter table public.profiles enable row level security;

-- Tenants: a client sees only its own row; the owner sees all.
create policy tenants_read on public.tenants
    for select using (
        app.is_platform_owner() or id = app.current_tenant_id()
    );

-- Only the platform owner creates or edits tenants. Clients are added by hand;
-- there is no self-serve onboarding, so there is no reason for anyone else to write.
create policy tenants_owner_writes on public.tenants
    for all using (app.is_platform_owner())
    with check (app.is_platform_owner());

-- Profiles: you can always read yourself; otherwise you see your own tenant's team.
create policy profiles_read on public.profiles
    for select using (
        id = auth.uid()
        or app.is_platform_owner()
        or tenant_id = app.current_tenant_id()
    );

create policy profiles_owner_writes on public.profiles
    for all using (app.is_platform_owner())
    with check (app.is_platform_owner());
