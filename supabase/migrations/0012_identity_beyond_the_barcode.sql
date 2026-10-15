-- 0012: the barcode stops being identity all by itself
--
-- `identity_key` keyed an EAN row on the EAN and nothing else, on the theory that a
-- barcode names a product globally. It does — when the supplier used it correctly, and
-- a good many do not. In the sample corpus, ten EANs cover rows that are plainly not
-- the same stock:
--
--   8806097728955   Galaxy Tab S10 Lite 128GB Gray   249.00
--   8806097728955   Galaxy Tab S10 Lite 128GB Silver 245.00
--
-- Two finishes, two prices, one barcode. Keyed on the barcode alone they are one
-- identity, so reconciliation keeps the first and drops the second, and a real offer
-- never reaches the board. The same list does it with DE and EU stock, and one
-- supplier puts the base model's EAN on both the Enterprise Edition and the open-market
-- line — the exact pair the edition work exists to keep apart, undone by the EAN
-- branch skipping over it.
--
-- The same list makes the point a second way. It carries no EANs at all, and prices
-- Watch Series 11 42mm Silver at 355 and at 432 under one product name — the only
-- thing separating them is the part number, MF8J4MP/A against MF8J4WF/A. A
-- manufacturer part number names a product globally, exactly as an EAN does, so it
-- qualifies identity too.
--
-- A seller's own article number does not, and the difference is not cosmetic: one
-- supplier's 'Artikelnummer' column lists the same case twice at the same price under
-- A009571 and A010679 — two lines in their catalogue, one lot of stock. Keying on that
-- splits 19 rows that belong together. The column header is what tells the two apart;
-- see `_SELLERS_OWN_CODE` in app/brain/tables/columns.py. Only a part number reaches
-- the `part_number` column, which is why this expression can use it without a guard.
--
-- So capacity, colour, edition and part number now qualify the EAN as well as the spec
-- fallback.
-- They are the supplier's own structured statement of what the row is, and when those
-- disagree the rows are different stock whatever the barcode says. Grade and region
-- stay out, here as before: they describe the condition of a lot, not the product.
--
-- Matching is unaffected. It compares the `ean` column directly rather than this key
-- (see `_same_product` in app/brain/matching.py), so a US supplier's UPC still meets a
-- European EAN for the same handset.
--
-- Mirrors ProductSpec.identity_key() in app/brain/normalise/product.py. If one changes
-- and the other does not, reconciliation stops updating rows and starts creating
-- duplicates. tests/test_route_agreement.py holds the two to each other.

-- A generated column's expression cannot be altered in place, so the column is dropped
-- and rebuilt. Postgres recomputes it for every existing row, which is what we want:
-- the Python side and the stored side change together, in one deploy. The only rows
-- whose key actually moves are the ones that were colliding, and those were already
-- losing data.
--
-- The unique index goes with the column and has to be recreated. It can only get
-- easier to satisfy — the new key is strictly more discriminating than the old one —
-- so no existing pair of live rows can be in violation after this runs.

alter table public.offers
    add column if not exists part_number text;

comment on column public.offers.part_number is
    'The manufacturer''s part number, where a column gave one — MF8J4WF/A, '
    'SM-X130NZSEEUE. Part of identity: it names a product globally the way an EAN '
    'does. A seller''s own article number is kept in `code` and is NOT identity.';

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
            else 'spec:'
                 || lower(coalesce(brand, ''))           || '|'
                 || lower(coalesce(description_key, '')) || '|'
                 || coalesce(capacity_gb::text, '')      || '|'
                 || lower(coalesce(colour, ''))          || '|'
                 || lower(coalesce(edition, ''))         || '|'
                 || lower(coalesce(part_number, ''))
        end
    ) stored;

create unique index if not exists offers_live_identity_uniq
    on public.offers (tenant_id, counterparty_id, side, identity_key)
    where status = 'live';

comment on column public.offers.identity_key is
    'What makes two rows the same lot. EAN plus capacity, colour, edition and part number when the '
    'row has a barcode; brand plus normalised description plus the same four when it '
    'does not. Mirrors ProductSpec.identity_key() — the two must stay in step.';

comment on column public.offers.edition is
    'Enterprise Edition or Open Market, where the line says so. Part of identity: the '
    'two are different lots at different prices and suppliers list them consecutively.';
