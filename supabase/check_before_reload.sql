-- check_before_reload.sql -- run this before `load_samples.py --wipe`.
--
-- --wipe is not reversible, and the two things worth losing sleep over are not the
-- offers. Offers are re-derived from the stored emails on the next load. These are not:
--
--   deal_allocations        a human decided this buyer gets that seller's stock
--   counterparty settings   decimal separator, default currency, typical row count --
--                           learned or typed by hand, none of it re-derivable
--
-- If query 1 returns zeros on both counts, --wipe costs nothing. If it does not, say
-- so before running it.
--
-- Written for the Supabase SQL editor, which runs one statement at a time and shows
-- the last result -- so select a block and run it, rather than running the whole file.


-- 1. ---------------------------------------------------------------------------
-- What --wipe would destroy and the next load would not rebuild.
-- Both zero => safe to wipe.

select 'allocations a human made'      as what,
       (select count(*) from public.deal_allocations) as count
union all
select 'counterparties with settings',
       (select count(*) from public.counterparties
         where decimal_separator is not null
            or default_currency   is not null
            or typical_row_count  is not null);


-- 2. ---------------------------------------------------------------------------
-- Where the offers are actually filed, and the reason any of this is happening.
--
-- An address at @tvdservices.com near the top of this list IS the misattribution bug:
-- a staff member forwarding supplier mail, credited as the supplier. After the tenant's
-- internal_domains is set and the board reloaded, those rows should be gone and real
-- supplier domains should hold the top of the list.

select c.primary_email,
       c.name,
       count(*) filter (where o.status = 'live') as live_offers,
       count(*)                                  as all_offers
from public.offers o
join public.counterparties c on c.id = o.counterparty_id
group by c.primary_email, c.name
order by all_offers desc
limit 15;


-- 3. ---------------------------------------------------------------------------
-- How much of the board has no counterparty name. This is what shows on the Matches
-- tab as "unknown buyer" followed by a fragment of a UUID.

select count(*) filter (where c.name is null or c.name = '') as unnamed,
       count(*)                                              as total
from public.offers o
join public.counterparties c on c.id = o.counterparty_id;


-- 4. ---------------------------------------------------------------------------
-- How senders are being resolved. Once forwarding is live, expect 'envelope' to stay
-- dominant (Gmail auto-forward and Outlook Redirect both preserve the original From:)
-- with a tail of 'forwarded_header'. A large 'body_signature' or 'unresolved' share
-- means mail is arriving wrapped, or internal_addresses is still missing somebody.

select counterparty_method,
       count(*)                                        as emails,
       round(avg(counterparty_confidence)::numeric, 2) as avg_confidence
from public.emails
group by counterparty_method
order by emails desc;
