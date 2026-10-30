-- 0018: which categories a list covered
--
-- A supplier's list replaces their previous list only in the categories it covers, and
-- only the first whole list of the day in a category gets to replace anything; a later
-- one the same day adds and updates without closing (app/pipeline.py, "replace or
-- add"). Answering "has a whole list already covered phones today?" needs the
-- categories of each applied list, by the email's own clock.
--
-- Until this runs the backend still works: it notices the missing column, logs once,
-- and treats every same-day list as an addition (never closing), which is the safe
-- side. Safe to run more than once.

alter table public.imports
    add column if not exists categories text[];

comment on column public.imports.categories is
    'Categories the list covered ('''''' for rows with none). Written on every applied import.';


-- The Counterparties tab counts suppliers that appeared since you last looked, live.
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and tablename = 'counterparties'
    ) then
        alter publication supabase_realtime add table public.counterparties;
    end if;
end $$;
