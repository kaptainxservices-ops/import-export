-- n8n_database.sql — give n8n its own corner of the Postgres instance.
--
-- Run this ONCE in the Supabase SQL editor, before creating the n8n service.
--
-- n8n keeps its own state in Postgres: workflows, credentials, execution history. It
-- does not need a second database, but it must not share a schema with the application
-- — n8n runs its own migrations, creates and drops its own tables, and a name collision
-- with `offers` or `emails` is not a conversation worth having at 3am.
--
-- A separate role as well as a separate schema. The application's service key already
-- bypasses row-level security, so handing n8n that same key would give a workflow
-- editor — a GUI, edited by whoever is in a hurry — unrestricted read and write over
-- every client's board. One mistyped node should not be able to empty `offers`.

-- ---------------------------------------------------------------------------
-- 1. The role
-- ---------------------------------------------------------------------------
-- CHANGE THIS PASSWORD before running. It goes into Render as DB_POSTGRESDB_PASSWORD
-- and nowhere else.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'n8n_user') then
        create role n8n_user with login password 'CHANGE_ME_BEFORE_RUNNING';
    end if;
end $$;


-- ---------------------------------------------------------------------------
-- 2. The schema
-- ---------------------------------------------------------------------------

create schema if not exists n8n authorization n8n_user;

-- Everything n8n needs, inside its own schema and nowhere else.
grant usage, create on schema n8n to n8n_user;


-- ---------------------------------------------------------------------------
-- 3. Keep it out of everything else
-- ---------------------------------------------------------------------------
-- The point of the separate role. Without these, n8n_user inherits whatever PUBLIC can
-- do, and on a default Postgres that is more than nothing.

revoke all on schema public from n8n_user;
revoke all on all tables    in schema public from n8n_user;
revoke all on all sequences in schema public from n8n_user;
revoke all on all functions in schema public from n8n_user;

-- And on anything created in public later. Without this the revoke above protects
-- today's tables and silently fails to protect tomorrow's.
alter default privileges in schema public
    revoke all on tables    from n8n_user;
alter default privileges in schema public
    revoke all on sequences from n8n_user;

-- n8n connects with `search_path` set to its own schema, so it never has to be told
-- which schema to use and never accidentally resolves a name in public.
alter role n8n_user set search_path = n8n;


-- ---------------------------------------------------------------------------
-- 4. Check it worked
-- ---------------------------------------------------------------------------
-- Expect: the schema exists, owned by n8n_user, and n8n_user cannot read public.offers.

select
    (select count(*) from pg_namespace where nspname = 'n8n')            as schema_exists,
    (select count(*) from pg_roles     where rolname = 'n8n_user')       as role_exists,
    has_table_privilege('n8n_user', 'public.offers', 'SELECT')           as can_read_offers;
-- schema_exists 1, role_exists 1, can_read_offers FALSE
