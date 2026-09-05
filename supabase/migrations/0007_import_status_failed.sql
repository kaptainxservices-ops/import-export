-- 0007_import_status_failed.sql
--
-- `reconcile()` has four outcomes and the enum only ever had three.
--
-- The missing one is 'failed': an email arrived, nothing could be extracted from it, and
-- so nothing was closed. That is not an error to be swallowed — it is the single most
-- useful row in the imports table, because it is what a supplier changing their format
-- looks like on the morning it happens. Fourteen of the 45 sample emails produce it, and
-- every one of them was being rejected by Postgres *after* its offers had been written,
-- leaving the import unrecorded and the failure invisible.
--
-- Widening the enum rather than narrowing the code, because the state is real. Mapping it
-- onto 'applied' would say a list was applied when nothing was; onto
-- 'flagged_low_row_count' would say the row count was low when there was no row count.
--
-- Safe to re-run.

do $$
begin
    if not exists (
        select 1
        from pg_enum e
        join pg_type t on t.oid = e.enumtypid
        where t.typname = 'import_status' and e.enumlabel = 'failed'
    ) then
        alter type public.import_status add value 'failed';
    end if;
end $$;

comment on type public.import_status is
    'applied: the list was reconciled. flagged_*: it was reconciled but nothing was '
    'closed, because the list looked partial. failed: nothing could be extracted at '
    'all — the email is stored, the board is untouched, and somebody should look.';
