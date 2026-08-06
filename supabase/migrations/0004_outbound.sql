-- 0004_outbound.sql
-- Outbound email, replies, and the conversations replies open. (Checklist phase 6.)
--
-- Sending is manual and reviewed; updating from replies is automatic. That asymmetry
-- is the whole design. The rule replies follow:
--
--     facts update the row, negotiations do not.
--
-- "Sold out" or "price is 890 on the same lot" is a fact: update the offer and log it.
-- A counter-offer or a conditional term is a negotiation: never overwrite, open a
-- conversation and badge the row so nobody chases the same lot twice. Anything
-- ambiguous is treated as a negotiation, because that is the direction that destroys
-- no data.

create table public.templates (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,

    name                text not null,
    subject             text not null,
    body                text not null,          -- supports {{placeholders}}
    is_active           boolean not null default true,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    unique (tenant_id, name)
);

create trigger templates_touch
    before update on public.templates
    for each row execute function app.touch_updated_at();


create type public.outbound_status as enum ('draft', 'sent', 'failed');

create table public.outbound_messages (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    counterparty_id     uuid references public.counterparties (id) on delete set null,
    template_id         uuid references public.templates (id) on delete set null,

    -- Which offers this message is about. Array rather than a join table: a message
    -- references a handful of offers and is never queried from the offer side.
    offer_ids           uuid[] not null default '{}',

    to_emails           text[] not null default '{}',
    subject             text not null,
    body                text not null,

    status              public.outbound_status not null default 'draft',
    message_id          text,                   -- assigned once actually sent
    thread_id           text,
    sent_at             timestamptz,
    sent_by             uuid references public.profiles (id) on delete set null,
    error               text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index outbound_tenant_idx on public.outbound_messages (tenant_id, created_at desc);
create index outbound_thread_idx on public.outbound_messages (tenant_id, thread_id);

create trigger outbound_touch
    before update on public.outbound_messages
    for each row execute function app.touch_updated_at();


-- ---------------------------------------------------------------------------
-- Conversations — where negotiations land
-- ---------------------------------------------------------------------------
-- Deliberately thin: which offer, who, what was said, a note, mark done. No
-- allocations, no margin maths, no multi-party deals. That drift is what turns a
-- nine-day build into a three-week one.

create type public.conversation_state as enum ('open', 'done', 'dead');

create table public.conversations (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references public.tenants (id) on delete cascade,
    offer_id            uuid references public.offers (id) on delete set null,
    counterparty_id     uuid references public.counterparties (id) on delete set null,

    state               public.conversation_state not null default 'open',
    summary             text,
    note                text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index conversations_open_idx on public.conversations (tenant_id, state)
    where state = 'open';

create trigger conversations_touch
    before update on public.conversations
    for each row execute function app.touch_updated_at();


alter table public.templates         enable row level security;
alter table public.outbound_messages enable row level security;
alter table public.conversations     enable row level security;

create policy templates_tenant on public.templates
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy outbound_tenant on public.outbound_messages
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());

create policy conversations_tenant on public.conversations
    for all using (app.is_platform_owner() or tenant_id = app.current_tenant_id())
    with check (app.is_platform_owner() or tenant_id = app.current_tenant_id());
