-- 0010_known_company_sender.sql
--
-- A sixth way to know who sent an email: the company named in the subject matches a
-- counterparty somebody has already identified.
--
-- About one email in five arrives with no address anywhere in it — offers a staff
-- member retyped out of WhatsApp. `subject_company` is the right answer the first time
-- and the wrong one the twentieth: once a person has said Vadimpex is
-- sales@vadimpex.example, asking again every morning is a tax, not caution.
--
-- Kept distinct from both neighbours on purpose. It is not `manual` — no person touched
-- this email. It is not `envelope` — the address was never in the message. Collapsing
-- it into either would lose the ability to ask, later, how much of the board rests on
-- a name match rather than on an address.

do $$
begin
    if not exists (
        select 1 from pg_enum e
        join pg_type t on t.oid = e.enumtypid
        where t.typname = 'sender_method' and e.enumlabel = 'known_company'
    ) then
        alter type public.sender_method add value 'known_company';
    end if;
end $$;

comment on type public.sender_method is
    'How the sender was established, worst to best: unresolved, subject_company, '
    'body_signature, known_company, forwarded_header, envelope, manual.';
