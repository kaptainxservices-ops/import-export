-- 0013: a second list on the same day waits for a person
--
-- Suppliers revise. They send the morning's stock list, sell three lots by lunchtime,
-- and send it again. Until now the second list was reconciled exactly like the first,
-- which means every row on the morning list that is absent from the afternoon one was
-- closed as sold. Sometimes that is right. Sometimes the afternoon email was 'here are
-- the 40 iPhones I forgot', and reading it as a complete list closed the other 450.
--
-- The client's rule: the first list of the day goes on the board, and anything further
-- from the same counterparty that day is held for a person, who says whether it is the
-- same list, a new list, or a partial update.
--
-- Why the day and not a rolling 24 hours. A rolling window misfires on the commonest
-- pattern in the trade: a supplier who sends daily at roughly the same time, a little
-- earlier each day. 09:00 Monday and 08:30 Tuesday is 23.5 hours, so Tuesday's
-- perfectly ordinary list would be held — every day, for every punctual supplier, until
-- nobody opens the queue. A calendar day has no such edge, and 'the first list of the
-- day goes on, the rest wait for you' is a sentence a person can hold in their head.
--
-- The cost is the midnight seam: a revision at 23:50 and another at 00:10 falls in two
-- days, so the second applies without asking. That is what happens today for every
-- revision, so nothing gets worse; it is just not improved.

-- ---------------------------------------------------------------------------
-- Whose day
-- ---------------------------------------------------------------------------
-- 'Today' is meaningless without somewhere to stand. The board belongs to the client,
-- so it is the client's day — not the supplier's, who may be in Dubai sending at 02:00
-- their time, which is still the previous evening in Rotterdam.

alter table public.tenants
    add column if not exists timezone text not null default 'Europe/Amsterdam';

comment on column public.tenants.timezone is
    'IANA zone deciding when this tenant''s day rolls over, which is what makes a '
    'second list "the same day". TVD B.V. is in Rotterdam; the UK entity would be '
    'Europe/London. Not cosmetic: it decides whether an evening revision is held for '
    'review or applied straight to the board.';


-- ---------------------------------------------------------------------------
-- Which side a list was
-- ---------------------------------------------------------------------------
-- The hold is scoped to counterparty AND side. A supplier who sends a stock list in the
-- morning and a buying list at noon has not resent anything — those are two different
-- statements about two different things, and holding the second would be a queue entry
-- that never needed a person.
--
-- `imports` never recorded side. It could be reached through email_id -> emails ->
-- classification, but that is a join on every incoming list to answer a question the
-- import already knew the answer to when it was written.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'imports' and column_name = 'side'
    ) then
        alter table public.imports add column side public.offer_side;

        -- Backfill from the email that produced each import. 'both' and the
        -- non-committal classifications leave it null, which is correct: null means
        -- 'we do not know what side this was', and the same-day lookup must not match
        -- on it. The cost of a null is one list that is applied instead of held, which
        -- is exactly today's behaviour.
        update public.imports i
        set side = case e.classification
                       when 'seller_offer'  then 'sell'::public.offer_side
                       when 'buyer_request' then 'buy'::public.offer_side
                   end
        from public.emails e
        where e.id = i.email_id and i.side is null;
    end if;
end $$;

comment on column public.imports.side is
    'sell or buy, as this list was read. Null on historical rows and on any list whose '
    'side was never settled. The same-day hold is scoped by it, so a null simply never '
    'matches — a supplier''s stock list and their buying list do not hold each other.';


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


-- ---------------------------------------------------------------------------
-- What the list said
-- ---------------------------------------------------------------------------
-- The hash of identity, quantity, price and currency across the rows — see
-- app/brain/fingerprint.py. It answers 'does this list say the same thing as the one we
-- already hold?', which is the question behind both jobs this migration serves:
--
--   * the same email arriving twice through two collectors, when Outlook Forward
--     wrapped it and gave the copy a new Message-ID, so message_id dedupe cannot see it
--   * a supplier resending the same list unchanged, which needs no human decision
--
-- Both end in the same action — refresh the timestamps, reconcile nothing — which is
-- why one column answers both.

alter table public.imports
    add column if not exists content_hash text,
    -- When the EMAIL arrived, not when this row was written.
    --
    -- The same-day rule compares an arriving email's received_at against the last
    -- applied import. Comparing it against created_at instead means two different
    -- clocks: an email that arrived at 23:55 and was processed at 00:10 — an n8n poll
    -- delay, or a backlog after an outage — is recorded as the next day's list, and
    -- the revision that follows it is applied straight to the board instead of being
    -- held. Found by a test that passed for eleven hours and failed at midnight.
    add column if not exists source_received_at timestamptz,
    -- The import this one was compared against. Null for a first-of-day list, which is
    -- compared against nothing.
    add column if not exists compared_to_import_id uuid
        references public.imports (id) on delete set null,
    -- 'identical', or '3 new, 1 gone, 12 changed' — the one line that tells someone
    -- whether opening the diff is worth the trip.
    add column if not exists difference_summary text,
    -- Set when a person resolves a held import, so the queue can show only what is
    -- still waiting and an audit can see who decided what.
    add column if not exists resolved_at timestamptz,
    add column if not exists resolved_by uuid references auth.users (id) on delete set null,
    add column if not exists resolution text;

comment on column public.imports.content_hash is
    'Fingerprint of identity+quantity+price+currency over the rows. Deliberately not '
    'the description: a supplier retyping a product name has changed nothing a broker '
    'would act on, and treating that as a new list sends someone to compare two '
    'identical boards.';
comment on column public.imports.resolution is
    'How a held import was settled: same | new | partial. Null while it waits.';
comment on column public.imports.source_received_at is
    'The arrival time of the email behind this import, which is what the same-day rule '
    'compares against. created_at is when the row was written and drifts from it '
    'whenever ingestion is behind.';


-- ---------------------------------------------------------------------------
-- Where a held list keeps its rows
-- ---------------------------------------------------------------------------
-- A held import has parsed rows and has applied none of them. They have to live
-- somewhere until a person decides, and they cannot live in `offers` — everything that
-- reads that table treats a live row as real stock a broker may quote.

create table if not exists public.held_rows (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references public.tenants (id) on delete cascade,
    import_id       uuid not null references public.imports (id) on delete cascade,

    -- The same shape apply_actions writes, so resolving a held import is the ordinary
    -- path with a different trigger rather than a second implementation of it.
    identity_key    text not null,
    description     text,
    quantity        integer,
    unit_price      numeric(14, 4),
    currency        text,
    fields          jsonb not null default '{}'::jsonb,
    source_ref      text,

    -- What this row would do to the board, worked out when the list was parsed so the
    -- review screen does not recompute it on every open: insert | update | close |
    -- refresh, and the live offer it would act on.
    action          text,
    offer_id        uuid references public.offers (id) on delete set null,
    previous_quantity  integer,
    previous_price     numeric(14, 4),

    -- Set when a person applies this row and leaves the rest.
    applied_at      timestamptz,

    created_at      timestamptz not null default now()
);

create index if not exists held_rows_import_idx on public.held_rows (import_id);

comment on table public.held_rows is
    'Rows of a list that was parsed but not applied, waiting for a person to say what a '
    'second same-day list means. Deliberately NOT in `offers`: everything reading that '
    'table treats a live row as stock a broker may quote, and a row nobody has approved '
    'is not that.';

alter table public.held_rows enable row level security;

-- Read-only to clients, like `emails` and `imports` and for the same reason: these rows
-- are what a person is shown before deciding, and evidence a user can edit stops being
-- evidence. The backend writes them with the service role, which bypasses RLS.
drop policy if exists held_rows_read on public.held_rows;
create policy held_rows_read on public.held_rows
    for select using (app.is_platform_owner() or tenant_id = app.current_tenant_id());


-- The review queue reads exactly these, and they are a small fraction of the table.
create index if not exists imports_held_idx
    on public.imports (tenant_id, created_at desc)
    where status = 'held_for_review' and resolved_at is null;

-- The lookup on every incoming list: has this counterparty and side already had one
-- applied today?
create index if not exists imports_applied_lookup_idx
    on public.imports (tenant_id, counterparty_id, side, source_received_at desc)
    where status in ('applied', 'flagged_low_row_count', 'flagged_partial_list');
