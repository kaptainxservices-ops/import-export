-- An email that reached the backend after a newer whole list from the same sender had
-- already been applied (n8n catching up after an outage delivers mail out of order).
-- Everything in it is older than the board, so it is recorded and applied to nothing.
-- Safe to run more than once.
do $$
begin
    if not exists (
        select 1 from pg_enum e join pg_type t on t.oid = e.enumtypid
        where t.typname = 'import_status' and e.enumlabel = 'superseded'
    ) then
        alter type public.import_status add value 'superseded';
    end if;
end $$;

comment on type public.import_status is
    'applied — reconciled onto the board. flagged_* — applied, but closure was refused. '
    'failed — nothing extracted. held_for_review — a second list the same day, parsed '
    'and stored, nothing applied, waiting for a person. same_as_applied — identical to '
    'a list applied today; timestamps refreshed only. superseded — arrived after a newer '
    'whole list from the same sender; applied to nothing.';
