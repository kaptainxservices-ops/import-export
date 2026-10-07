-- setup_tenant.sql — create a client and give a login access to it.
--
-- Until the admin panel exists this is how a tenant is created. Row-level security
-- decides what a signed-in user can see by looking up their `profiles` row, so a user
-- with no profile sees an empty board rather than an error — which looks like a broken
-- dashboard and is really a missing row.
--
-- HOW TO RUN
--   1. Supabase > Authentication > Users > Add user. Create the login, note its UID.
--   2. Paste the UID and the client's details below.
--   3. Run the whole file.
--
-- Safe to re-run: it updates rather than duplicating.

do $$
declare
    -- ---- fill these in -----------------------------------------------------
    user_id       uuid := '47895e02-d6c5-4f28-94f6-b0c36dc0dc55';
    user_email    text := 'shahardik1165@gmail.com';
    client_name   text := 'TVD Services';
    -- The tenant is found by name, so renaming one without saying so here would not
    -- rename anything -- it would quietly create a second tenant alongside the first
    -- and leave every existing offer stranded on the old one, invisible to the login
    -- this script then points at the new one. Set this to the name it is stored under
    -- today and the rename happens in place. Leave it null once the rename is done.
    previous_name text := 'TVD Services (test)';

    -- Addresses that belong to the client themselves. Used to tell a supplier's email
    -- from one the client's own staff sent; it cannot be detected, only configured.
    --
    -- GET THIS WRONG AND THE BOARD IS WRONG. resolve_sender reads the envelope first
    -- and only looks for a forwarded From: block when the envelope turns out to be
    -- internal. Leave these empty and every message a staff member forwards is filed
    -- against that staff member: on the first load that put 3,252 offers under
    -- sales@tvdservices.com, as though one colleague were the client's largest
    -- supplier. The whole collector-mailbox design depends on this list being
    -- complete.
    --
    -- The domain covers info@, sales@, sales1@ and anything added later. Staff who
    -- send from somewhere else need listing by address -- including the collector
    -- mailbox itself, which relays everything and would otherwise look like the
    -- busiest supplier of all.
    own_domains   text[] := array['tvdservices.com'];
    own_addresses text[] := array[
        'tvdlogistics@outlook.com'
        -- , '<the collector mailbox>'   -- add once it exists
        -- , '<the fifth staff address>' -- still outstanding from the client
    ];
    -- Make this login the platform owner (you) rather than a client user.
    is_owner      boolean := true;
    -- ------------------------------------------------------------------------

    tenant uuid;
begin
    if not exists (select 1 from auth.users where id = user_id) then
        raise exception
            'No auth user with id %. Create the login first, then paste its UID here.',
            user_id;
    end if;

    select id into tenant from public.tenants where name = client_name;

    if tenant is null and previous_name is not null then
        select id into tenant from public.tenants where name = previous_name;
        if tenant is not null then
            update public.tenants set name = client_name where id = tenant;
            raise notice 'renamed tenant % -> % (%)', previous_name, client_name, tenant;
        end if;
    end if;

    if tenant is null then
        insert into public.tenants (name, internal_domains, internal_addresses)
        values (client_name, own_domains, own_addresses)
        returning id into tenant;
        raise notice 'created tenant % (%)', client_name, tenant;
    else
        update public.tenants
           set internal_domains = own_domains,
               internal_addresses = own_addresses
         where id = tenant;
        raise notice 'updated existing tenant % (%)', client_name, tenant;
    end if;

    -- The platform owner sits above every tenant and so has no tenant_id; a client
    -- user must have exactly one. The check constraint on `profiles` enforces it.
    insert into public.profiles (id, tenant_id, role, email)
    values (
        user_id,
        case when is_owner then null else tenant end,
        case when is_owner then 'platform_owner' else 'client_admin' end::public.user_role,
        user_email
    )
    on conflict (id) do update
        set tenant_id = excluded.tenant_id,
            role      = excluded.role,
            email     = excluded.email;

    raise notice 'profile ready for % as %', user_email,
        case when is_owner then 'platform_owner' else 'client_admin' end;
    raise notice 'TENANT ID (n8n will need this): %', tenant;
end $$;


-- Realtime is opt-in per table. Without this the dashboard still works, but the board
-- only updates when the page is reloaded — which is exactly the manual checking the
-- product exists to remove.
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and tablename = 'offers'
    ) then
        alter publication supabase_realtime add table public.offers;
        raise notice 'realtime enabled for offers';
    end if;
end $$;


select id as tenant_id, name, created_at from public.tenants order by created_at;
