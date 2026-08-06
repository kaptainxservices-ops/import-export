-- rls_isolation.sql — checklist step 11.
--
-- Proves that two tenants cannot see each other's data, BEFORE anything is built on
-- top of the schema. Tenant isolation is the one thing that is catastrophic to get
-- wrong and expensive to retrofit: a leak means one broker sees another broker's
-- supplier prices, which is the client's entire commercial advantage.
--
-- Everything happens inside a transaction that rolls back, so this leaves no data
-- behind and is safe to run repeatedly.
--
-- HOW TO RUN
--   1. Supabase dashboard > Authentication > Users > Add user. Create two users with
--      any email and password, e.g. alpha@test.local and beta@test.local.
--   2. Copy each user's UUID from that same screen.
--   3. Paste them below, replacing the two placeholder UUIDs.
--   4. Run the whole file in the SQL editor.
--
-- PASS looks like a single row reading "RLS ISOLATION PASSED".
-- FAIL raises an exception naming the check that failed, and the final SELECT never
-- runs — an exception aborts the whole transaction. Do not continue on a fail.
--
-- The RAISE NOTICE at the end goes to the Postgres log rather than the results grid,
-- which is why the final SELECT exists: a visible row is worth more than a silent
-- "Success. No rows returned", which looks identical to a script that did nothing.

begin;

do $$
declare
    -- ---- paste the two user UUIDs here -------------------------------------
    user_alpha uuid := '622a6113-be70-46fb-bae4-d982ef6eb060';
    user_beta  uuid := '2c10e4a2-d5dd-4ea7-9047-62bae8da507b';
    -- ------------------------------------------------------------------------

    tenant_alpha uuid;
    tenant_beta  uuid;
    cp_alpha     uuid;
    cp_beta      uuid;
    seen         integer;
    leaked       integer;
begin
    if not exists (select 1 from auth.users where id = user_alpha)
       or not exists (select 1 from auth.users where id = user_beta) then
        raise exception
            'Create two auth users first and paste their UUIDs at the top of this file.';
    end if;

    -- Two tenants, one user each, one counterparty and one offer each.
    insert into public.tenants (name) values ('Test Alpha') returning id into tenant_alpha;
    insert into public.tenants (name) values ('Test Beta')  returning id into tenant_beta;

    insert into public.profiles (id, tenant_id, role, email)
    values (user_alpha, tenant_alpha, 'client_admin', 'alpha@test.local')
    on conflict (id) do update
        set tenant_id = excluded.tenant_id, role = excluded.role;

    insert into public.profiles (id, tenant_id, role, email)
    values (user_beta, tenant_beta, 'client_admin', 'beta@test.local')
    on conflict (id) do update
        set tenant_id = excluded.tenant_id, role = excluded.role;

    insert into public.counterparties (tenant_id, name, primary_email)
    values (tenant_alpha, 'Alpha Supplier', 'supplier@alpha.test') returning id into cp_alpha;

    insert into public.counterparties (tenant_id, name, primary_email)
    values (tenant_beta, 'Beta Supplier', 'supplier@beta.test') returning id into cp_beta;

    insert into public.offers
        (tenant_id, counterparty_id, side, model, storage_gb, colour, grade, region_code,
         quantity, unit_price, currency)
    values
        (tenant_alpha, cp_alpha, 'sell', 'iPhone 15 Pro Max', 256, 'Blue', 'A', 'LL/A',
         40, 905.00, 'USD'),
        (tenant_beta, cp_beta, 'sell', 'iPhone 14 Pro', 128, 'Black', 'A', 'ZP/A',
         25, 610.00, 'USD');

    -- ---- Act as Alpha ------------------------------------------------------
    -- This is how Supabase presents a signed-in user to Postgres: the role plus a JWT
    -- claim carrying the user id, which is what auth.uid() reads.
    set local role authenticated;
    perform set_config('request.jwt.claims',
                       json_build_object('sub', user_alpha, 'role', 'authenticated')::text,
                       true);

    select count(*) into seen   from public.offers where tenant_id = tenant_alpha;
    select count(*) into leaked from public.offers where tenant_id = tenant_beta;

    if seen <> 1 then
        raise exception 'FAIL: Alpha sees % of its own offers, expected 1', seen;
    end if;
    if leaked <> 0 then
        raise exception 'FAIL: Alpha can see % of Beta''s offers', leaked;
    end if;

    select count(*) into leaked from public.counterparties where tenant_id = tenant_beta;
    if leaked <> 0 then
        raise exception 'FAIL: Alpha can see Beta''s counterparties';
    end if;

    select count(*) into leaked from public.tenants where id = tenant_beta;
    if leaked <> 0 then
        raise exception 'FAIL: Alpha can see the Beta tenant row';
    end if;

    -- Writing into someone else's tenant must be refused by the WITH CHECK clause.
    begin
        insert into public.counterparties (tenant_id, name, primary_email)
        values (tenant_beta, 'Injected', 'inject@alpha.test');
        raise exception 'FAIL: Alpha wrote a row into Beta''s tenant';
    exception
        when insufficient_privilege then null;   -- expected
    end;

    reset role;

    -- ---- Act as Beta -------------------------------------------------------
    set local role authenticated;
    perform set_config('request.jwt.claims',
                       json_build_object('sub', user_beta, 'role', 'authenticated')::text,
                       true);

    select count(*) into seen   from public.offers where tenant_id = tenant_beta;
    select count(*) into leaked from public.offers where tenant_id = tenant_alpha;

    if seen <> 1 then
        raise exception 'FAIL: Beta sees % of its own offers, expected 1', seen;
    end if;
    if leaked <> 0 then
        raise exception 'FAIL: Beta can see % of Alpha''s offers', leaked;
    end if;

    reset role;

    -- ---- Anonymous sees nothing -------------------------------------------
    set local role anon;
    perform set_config('request.jwt.claims', null, true);

    select count(*) into leaked from public.offers;
    if leaked <> 0 then
        raise exception 'FAIL: an unauthenticated caller can read % offers', leaked;
    end if;

    reset role;

    raise notice 'RLS ISOLATION PASSED — 8 checks, no leakage in either direction';
end $$;

-- Only reached if every check above passed.
select 'RLS ISOLATION PASSED — 8 checks, no leakage in either direction' as result;

rollback;
