-- 0013: the two statuses a list can have when nothing was applied
--
-- On its own in this file, and that is the whole point. Postgres refuses to USE a new
-- enum value in the same transaction that added it:
--
--     ERROR: 55P04: unsafe use of new value "held_for_review" of enum type import_status
--     HINT:  New enum values must be committed before they can be used.
--
-- The Supabase SQL editor runs a script as one transaction, so an ALTER TYPE ... ADD
-- VALUE and a partial index whose predicate names that value cannot travel together.
-- Migration 0007 adds a value the same way and succeeds only because nothing in that
-- file goes on to use it.
--
-- So: run this one, then 0014. Two statements, one commit, and the value exists for
-- everything that follows.

-- ---------------------------------------------------------------------------
-- What a held import looks like
-- ---------------------------------------------------------------------------
-- Both of these applied NOTHING to the offers table, which is the difference from every
-- status that came before: until now an import was always applied and could only be
-- undone afterwards by rejecting it.

do $$
begin
    if not exists (
        select 1 from pg_enum e join pg_type t on t.oid = e.enumtypid
        where t.typname = 'import_status' and e.enumlabel = 'held_for_review'
    ) then
        alter type public.import_status add value 'held_for_review';
    end if;

    if not exists (
        select 1 from pg_enum e join pg_type t on t.oid = e.enumtypid
        where t.typname = 'import_status' and e.enumlabel = 'same_as_applied'
    ) then
        alter type public.import_status add value 'same_as_applied';
    end if;
end $$;

comment on type public.import_status is
    'applied — reconciled onto the board. flagged_* — applied, but closure was refused. '
    'failed — nothing extracted. held_for_review — a second list the same day, parsed '
    'and stored, nothing applied, waiting for a person. same_as_applied — a second list '
    'the same day whose rows were identical, so timestamps were refreshed and nothing '
    'else touched; decided without asking.';
