-- 0015: a lot is also its grade, its market and whether it has arrived
--
-- Whitehawk's Samsung sheet of 26 October listed 212 lines. 100 reached the board.
--
--   AA  SAM S24FE  128G  Graphite  14 @ 295
--   B   SAM S24FE  128G  Graphite  28 @ 255
--   C   SAM S24FE  128G  Graphite  17 @ 210
--
-- Three lots, three prices -- and one identity, because grade was kept out of
-- `identity_key` on the theory that it describes a lot rather than names a product. It
-- does describe a lot, and `identity_key` is exactly the thing that says which lot a
-- row is. Reconciliation keeps the first line of each identity, so the AA line stood
-- and 59 B- and C-grade handsets were never offered to anyone.
--
-- Region is the same failure for the same reason: an India-spec and an EU-spec lot of
-- one handset are priced apart and sold to different buyers. And availability is new:
-- the same email carried 423 JBL Flip Essential 2 'INCOMING (ETA 7-15 DAYS)' and 50
-- in the warehouse under one barcode. Keyed together, one of the two lots vanished.
--
-- Matching is unaffected by this. It compares products across grades, markets and
-- availability and says when they differ (see app/brain/matching.py); this only stops
-- one lot overwriting another.
--
-- Mirrors ProductSpec.identity_key() in app/brain/normalise/product.py, held to it by
-- tests/test_identity_agreement.py. Existing rows are re-keyed by Postgres from columns
-- they already have -- `grade` and `region_code` have been written since 0001, and
-- `availability` starts empty, which is what in-stock rows should carry.

alter table public.offers
    add column if not exists availability text,
    add column if not exists availability_note text;

comment on column public.offers.availability is
    '''incoming'' for stock still in transit, read from the sheet title, file name or a '
    'section heading. Empty for stock on hand. Part of identity.';
comment on column public.offers.availability_note is
    'The supplier''s own words about arrival, e.g. ''ETA 7-15 DAYS''. Not identity.';

drop index if exists offers_live_identity_uniq;

alter table public.offers drop column if exists identity_key;

alter table public.offers
    add column if not exists identity_key text generated always as (
        case
            when ean is not null and ean <> ''
                then 'ean:' || ean
                     || '|' || coalesce(capacity_gb::text, '')
                     || '|' || lower(coalesce(colour, ''))
                     || '|' || lower(coalesce(edition, ''))
                     || '|' || lower(coalesce(part_number, ''))
                     || '|' || lower(coalesce(grade, ''))
                     || '|' || lower(coalesce(region_code, ''))
                     || '|' || lower(coalesce(availability, ''))
            else 'spec:'
                 || lower(coalesce(brand, ''))           || '|'
                 || lower(coalesce(description_key, '')) || '|'
                 || coalesce(capacity_gb::text, '')      || '|'
                 || lower(coalesce(colour, ''))          || '|'
                 || lower(coalesce(edition, ''))         || '|'
                 || lower(coalesce(part_number, ''))     || '|'
                 || lower(coalesce(grade, ''))           || '|'
                 || lower(coalesce(region_code, ''))     || '|'
                 || lower(coalesce(availability, ''))
        end
    ) stored;

-- Live rows that collide under the new key cannot exist: the new key is strictly more
-- discriminating than the old one, so every pair that was distinct stays distinct.
create unique index if not exists offers_live_identity_uniq
    on public.offers (tenant_id, counterparty_id, side, identity_key)
    where status = 'live';

comment on column public.offers.identity_key is
    'What makes two rows the same lot. EAN plus capacity, colour, edition, part number, '
    'grade, region and availability when the row has a barcode; brand plus normalised '
    'description plus the same seven when it does not. Mirrors '
    'ProductSpec.identity_key() -- the two must stay in step.';
