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
-- So capacity, colour and edition now qualify the EAN as well as the spec fallback.
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
            else 'spec:'
                 || lower(coalesce(brand, ''))           || '|'
                 || lower(coalesce(description_key, '')) || '|'
                 || coalesce(capacity_gb::text, '')      || '|'
                 || lower(coalesce(colour, ''))          || '|'
                 || lower(coalesce(edition, ''))
        end
    ) stored;

create unique index if not exists offers_live_identity_uniq
    on public.offers (tenant_id, counterparty_id, side, identity_key)
    where status = 'live';

comment on column public.offers.identity_key is
    'What makes two rows the same lot. EAN plus capacity, colour and edition when the '
    'row has a barcode; brand plus normalised description plus the same three when it '
    'does not. Mirrors ProductSpec.identity_key() — the two must stay in step.';

comment on column public.offers.edition is
    'Enterprise Edition or Open Market, where the line says so. Part of identity: the '
    'two are different lots at different prices and suppliers list them consecutively.';
