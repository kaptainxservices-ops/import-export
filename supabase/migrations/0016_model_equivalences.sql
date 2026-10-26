-- 0016: when two wordings name one model, remember it
--
-- app/brain/normalise/canonical.py writes every model one way -- 'SAM S23', 'Samsung
-- Galaxy S23' and 'Galaxy S23 5G' are all samsung:s23 -- and that settles almost every
-- line deterministically. What it cannot settle is a leftover word it does not know:
-- 'OnePlus Nord CE6 Smooth' against 'OnePlus Nord CE6', 'JBL Tune 720BT Deluxe'
-- against 'JBL Tune 720BT'. Is the extra word a description, or a different product?
--
-- Those pairs are asked about once -- by the configured model, under a veto in code
-- that refuses any pair differing by a number or a variant word -- and the answer is
-- stored here, so the next list carrying the same wording is settled by a lookup. A
-- person can write a row too ('human'), and theirs is never overwritten by the model.
--
-- Keyed on the canonical model, not the raw description: every wording that reduces
-- to the same key shares one answer, so the table stays small and the question is
-- asked once per model rather than once per supplier.
--
-- Named model_equivalences, not model_aliases: 0003 already has a `model_aliases`
-- table (the shared iPhone catalogue). The first draft of this file reused that name;
-- if it was run, its `create table if not exists` was skipped and the statements after
-- it touched 0003's table before failing. The repair block below puts 0003's policy and
-- comment back exactly as 0003 wrote them. It is idempotent and harmless if nothing was
-- touched.

-- ---------------------------------------------------------------- repair 0003
drop policy if exists model_aliases_read on public.model_aliases;
create policy model_aliases_read on public.model_aliases
    for select using (auth.uid() is not null);

comment on table public.model_aliases is null;

-- ---------------------------------------------------------------- the new table
create table if not exists public.model_equivalences (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references public.tenants (id) on delete cascade,
    from_key    text not null,
    -- The model it is the same as, or NULL when the answer was 'a different product'.
    -- A stored 'no' matters as much as a 'yes': without it the same question is paid
    -- for on every list.
    to_key      text,
    source      text not null check (source in ('model', 'human')),
    model       text,
    note        text,
    created_at  timestamptz not null default now(),
    unique (tenant_id, from_key)
);

comment on table public.model_equivalences is
    'Two canonical model keys that name the same product (to_key set), or a confirmed '
    'difference (to_key null). Read by matching on every board load; written by '
    'app/brain/resolve_models.py and by people. A human row is never overwritten.';

alter table public.model_equivalences enable row level security;

drop policy if exists model_equivalences_read on public.model_equivalences;
create policy model_equivalences_read on public.model_equivalences
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());
