-- 0011_retention.sql
--
-- Nothing in this system has ever deleted anything.
--
-- Every email is stored whole: `body_text` for parsing and `body_raw`, the complete
-- original, kept only to render the source drawer. That is the right design for a
-- board somebody is trading off this morning. It is the wrong one for a message from
-- eighteen months ago, which has no trading value left and is purely a thing that can
-- leak.
--
-- The collector mailbox makes this urgent rather than tidy. Five staff mailboxes
-- forward everything they receive, so the store accumulates a full-text archive of
-- five people's working correspondence -- and the client is in Rotterdam and Reading,
-- so GDPR and UK GDPR apply, and both expect personal data not to be kept longer than
-- the purpose needs.
--
-- Two separate ages, because they answer different questions:
--
--   body_raw      the original message. Only ever used to show a human what arrived.
--                 Redacted first and soonest -- it is the largest and the most
--                 sensitive part of the row.
--
--   the email row the audit trail: who sent it, when, what was decided. An offer on
--                 today's board may still point at an email from months ago, so rows
--                 are deleted only once nothing references them.
--
-- Offers are never touched. A live offer is live regardless of how old the email that
-- produced it is, and deleting stock because its paperwork aged would be the worst
-- possible reading of "retention".

-- ---------------------------------------------------------------------------

create or replace function public.redact_old_email_bodies(older_than interval default '90 days')
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    affected integer;
begin
    -- The body goes; the row stays. After this the source drawer shows a note rather
    -- than the message, and everything the board is built from -- the parsed offers --
    -- is untouched.
    update public.emails
       set body_raw  = '',
           body_text = ''
     where received_at < now() - older_than
       and (body_raw <> '' or body_text <> '');

    get diagnostics affected = row_count;
    return affected;
end;
$$;

comment on function public.redact_old_email_bodies is
    'Blank the stored message of emails older than the given age, keeping the row. '
    'The body is the largest and most sensitive part and the first thing that should '
    'stop being kept.';


create or replace function public.delete_old_emails(older_than interval default '18 months')
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    affected integer;
begin
    -- Only once nothing points at it. An offer still on the board, or an import in
    -- the audit trail, is a reason to keep the row -- a dangling source_email_id turns
    -- "where did this price come from" into a shrug.
    delete from public.emails e
     where e.received_at < now() - older_than
       and not exists (select 1 from public.offers  o where o.source_email_id = e.id)
       and not exists (select 1 from public.imports i where i.email_id        = e.id);

    get diagnostics affected = row_count;
    return affected;
end;
$$;

comment on function public.delete_old_emails is
    'Remove emails nothing references any more. Offers and imports keep their source '
    'email alive however old it is, so this never breaks the audit trail.';


-- ---------------------------------------------------------------------------
-- Scheduling
-- ---------------------------------------------------------------------------
-- Deliberately NOT scheduled by this migration. Switching on an automatic delete as a
-- side effect of applying a migration is how somebody loses data they did not know was
-- at risk. Decide the windows with the client, then run this once:
--
--   create extension if not exists pg_cron;
--
--   select cron.schedule('redact-old-bodies', '30 3 * * *',
--       $cron$ select public.redact_old_email_bodies('90 days') $cron$);
--
--   select cron.schedule('delete-old-emails', '45 3 * * 0',
--       $cron$ select public.delete_old_emails('18 months') $cron$);
--
-- To see what either would do before letting it near anything:
--
--   select count(*) from public.emails
--    where received_at < now() - interval '90 days' and body_raw <> '';
