-- setup_shared_login.sql — give one extra login access to an EXISTING tenant.
--
-- Used for the shared demo account: a single email and password that anyone testing the
-- product can sign in with, so nobody has to be handed the owner's own credentials.
--
-- HOW TO RUN
--   1. Supabase > Authentication > Users > Add user.
--      Set the email and password, and tick "Auto Confirm User".
--      Without auto-confirm the account cannot sign in, and the dashboard will only say
--      "Those details were not accepted" — which looks like a wrong password and is
--      really an unconfirmed address.
--   2. Copy the new user's UID into user_id below, and set user_email to match.
--   3. Run the whole file.
--
-- Safe to re-run: the profile is updated rather than duplicated.
--
-- ⚠ WHAT THIS LOGIN CAN SEE
--   Everything on the tenant it is attached to. Row-level security isolates one tenant
--   from another; it does not isolate two users of the SAME tenant. Attaching a shared,
--   published password to a tenant therefore publishes that tenant's whole board —
--   every supplier, price, quantity and margin on it.
--
--   If that is not intended, point this at a tenant loaded only with the synthetic
--   emails in demo/ instead:
--       python scripts/load_samples.py --tenant <that tenant> --samples demo --wipe

do $$
declare
    -- ---- fill these in -----------------------------------------------------
    user_id    uuid := '00000000-0000-0000-0000-000000000000';
    user_email text := 'demo@kaptainx.dev';

    -- The tenant this login may read. The existing test tenant is
    -- b9316981-b732-4891-854f-3335205dd582.
    tenant     uuid := 'b9316981-b732-4891-854f-3335205dd582';

    -- client_admin can also change supplier parsing settings and resolve the review
    -- queue; client_user is read-mostly. Neither can reach another tenant.
    user_role  public.user_role := 'client_admin';
    -- ------------------------------------------------------------------------
begin
    if not exists (select 1 from auth.users where id = user_id) then
        raise exception
            'No auth user with id %. Create the login in Authentication > Users first, '
            'then paste its UID here.', user_id;
    end if;

    if not exists (select 1 from public.tenants where id = tenant) then
        raise exception 'No tenant with id %.', tenant;
    end if;

    -- A user with no profiles row sees an empty board rather than an error, because
    -- row-level security simply returns no rows. That looks like a broken dashboard and
    -- is really a missing row, so this is the line that matters.
    insert into public.profiles (id, tenant_id, role, email)
    values (user_id, tenant, user_role, user_email)
    on conflict (id) do update
        set tenant_id = excluded.tenant_id,
            role      = excluded.role,
            email     = excluded.email;

    raise notice 'profile ready: % is % on tenant %', user_email, user_role, tenant;
end $$;


-- Who can now see what. Every row here is a login that reaches a real board.
select p.email,
       p.role,
       coalesce(t.name, '(platform owner — all tenants)') as sees,
       p.created_at
  from public.profiles p
  left join public.tenants t on t.id = p.tenant_id
 order by p.created_at;
