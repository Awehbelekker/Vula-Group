-- Vula: migrations 298, 299 and 300 in one paste (24 Nov 2026).
-- Safe to run more than once. Paste ALL of it into an empty Supabase SQL editor and press Run.
-- One transaction: all of it applies, or none of it.
begin;

-- ===== 298_events_tickets.sql =====
create table if not exists commerce_events (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    title          text not null,
    description    text,
    venue          text,
    address        text,
    starts_at      timestamptz not null,
    ends_at        timestamptz,
    image_url      text,
    images         jsonb not null default '[]'::jsonb, -- the event's photos, the cover first
    addon_product_ids jsonb not null default '[]'::jsonb, -- products offered with the tickets (merch, parking)
    status         text not null default 'draft',      -- draft | on_sale | closed | cancelled
    sales_close_at timestamptz,                        -- null = until the event starts
    door_key       text unique,                        -- the door link's secret; null = no door link
    remind_buyers  boolean not null default true,      -- a WhatsApp reminder the day before
    thank_buyers   boolean not null default true,      -- a WhatsApp thank-you to those who came
    created_by     text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_events_tenant on commerce_events (tenant_id, starts_at);
alter table commerce_events enable row level security;
drop policy if exists "tenant_isolation" on commerce_events;
create policy "tenant_isolation" on commerce_events
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_ticket_types (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    event_id      uuid not null references commerce_events (id) on delete cascade,
    product_id    uuid not null,                        -- the commerce_products row that is sold
    name          text not null,
    capacity      integer not null check (capacity >= 0),
    max_per_order integer not null default 10 check (max_per_order between 1 and 50),
    sort_order    integer not null default 0,
    archived      boolean not null default false,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (tenant_id, product_id)
);
create index if not exists idx_ticket_types_event on commerce_ticket_types (tenant_id, event_id);
alter table commerce_ticket_types enable row level security;
drop policy if exists "tenant_isolation" on commerce_ticket_types;
create policy "tenant_isolation" on commerce_ticket_types
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_tickets (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    event_id       uuid not null references commerce_events (id) on delete cascade,
    ticket_type_id uuid not null references commerce_ticket_types (id),
    order_id       uuid not null,
    order_item_id  uuid not null,
    seq            integer not null,                    -- 1..quantity on that order line
    code           text not null unique,                -- what the QR carries
    holder_name    text,
    holder_phone   text,
    status         text not null default 'valid',       -- valid | checked_in | void
    checked_in_at  timestamptz,
    checked_in_by  text,
    reminded_at    timestamptz,                         -- the day-before reminder went to the buyer
    thanked_at     timestamptz,                         -- the after-event message went to the buyer
    voided_at      timestamptz,
    void_reason    text,
    created_at     timestamptz not null default now(),
    unique (order_item_id, seq)                         -- a line's tickets are issued once
);
create index if not exists idx_tickets_event on commerce_tickets (tenant_id, event_id, status);
create index if not exists idx_tickets_order on commerce_tickets (tenant_id, order_id);
alter table commerce_tickets enable row level security;
drop policy if exists "tenant_isolation" on commerce_tickets;
create policy "tenant_isolation" on commerce_tickets
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_tickets is
    'One row per ticket bought, issued when its order is paid. Checked in once; void when the order is cancelled or fully refunded.';

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('298_events_tickets.sql', 298, 'b03bc910b74da1b6b2ac5c295b8566a6e6301612f5bb6d3dc18aa06006956203', 'sql-editor', false) on conflict do nothing;

-- ===== 299_table_bookings_specials.sql =====
create table if not exists commerce_table_settings (
    tenant_id       text primary key,
    enabled         boolean not null default false,
    sittings        jsonb not null default '[]'::jsonb,   -- [{name, days:[0-6, Mon=0], start:"12:00", end:"15:00"}]
    slot_minutes    integer not null default 30 check (slot_minutes in (15, 30, 60)),
    seating_minutes integer not null default 120 check (seating_minutes between 30 and 480),
    max_covers      integer not null default 40 check (max_covers between 1 and 2000),
    max_party       integer not null default 10 check (max_party between 1 and 100),
    lead_minutes    integer not null default 60 check (lead_minutes between 0 and 10080),
    horizon_days    integer not null default 60 check (horizon_days between 1 and 365),
    note            text,                                  -- shown on the booking page ("Kitchen closes at 21:30")
    updated_at      timestamptz not null default now()
);
alter table commerce_table_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_table_settings;
create policy "tenant_isolation" on commerce_table_settings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_table_bookings (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    ref            text not null,                          -- what the customer quotes: T-4F7K
    starts_at      timestamptz not null,
    ends_at        timestamptz not null,
    party_size     integer not null check (party_size between 1 and 100),
    customer_name  text not null,
    customer_phone text,
    notes          text,
    status         text not null default 'confirmed',      -- confirmed | seated | completed | no_show | cancelled
    channel        text not null default 'web',            -- web | whatsapp | dashboard
    booked_by      text,
    reminded_at    timestamptz,
    cancelled_at   timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (tenant_id, ref)
);
create index if not exists idx_table_bookings_time on commerce_table_bookings (tenant_id, starts_at);
create index if not exists idx_table_bookings_phone on commerce_table_bookings (tenant_id, customer_phone);
alter table commerce_table_bookings enable row level security;
drop policy if exists "tenant_isolation" on commerce_table_bookings;
create policy "tenant_isolation" on commerce_table_bookings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_specials (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    title       text not null,
    description text,
    price_cents integer check (price_cents is null or price_cents >= 0),
    image_url   text,
    days        integer[] not null default '{}',           -- weekdays it runs (Mon=0); empty = every day
    starts_on   date,
    ends_on     date,
    active      boolean not null default true,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_specials_tenant on commerce_specials (tenant_id, active);
alter table commerce_specials enable row level security;
drop policy if exists "tenant_isolation" on commerce_specials;
create policy "tenant_isolation" on commerce_specials
    using (tenant_id = current_setting('app.tenant_id', true));

create or replace function book_table(
    p_tenant_id text, p_ref text, p_starts_at timestamptz, p_ends_at timestamptz, p_party integer,
    p_max_covers integer, p_name text, p_phone text, p_notes text, p_channel text, p_booked_by text)
returns uuid language plpgsql set search_path to 'public' as $$
declare
    v_taken integer;
    v_id uuid;
begin
    perform pg_advisory_xact_lock(hashtext('book_table:' || p_tenant_id));
    select coalesce(sum(party_size), 0) into v_taken
      from commerce_table_bookings
     where tenant_id = p_tenant_id and status in ('confirmed', 'seated')
       and starts_at < p_ends_at and ends_at > p_starts_at;
    if v_taken + p_party > p_max_covers then
        return null;
    end if;
    insert into commerce_table_bookings (tenant_id, ref, starts_at, ends_at, party_size, customer_name,
                                         customer_phone, notes, channel, booked_by)
    values (p_tenant_id, p_ref, p_starts_at, p_ends_at, p_party, p_name, p_phone, p_notes, p_channel, p_booked_by)
    returning id into v_id;
    return v_id;
end;
$$;
revoke all on function book_table(text, text, timestamptz, timestamptz, integer, integer, text, text, text, text, text) from public, anon, authenticated;

comment on table commerce_table_bookings is
    'A restaurant table booking: seats held from starts_at to ends_at. Made only through book_table().';
grant execute on function book_table(text, text, timestamptz, timestamptz, integer, integer, text, text, text, text, text) to service_role;

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('299_table_bookings_specials.sql', 299, '2da1bf2bb6b8d931d776d509a9750c1756d543ca9d94729362a996de72c8daa3', 'sql-editor', false) on conflict do nothing;

-- ===== 300_table_service.sql =====
alter table kb_tags add column if not exists label text;
alter table kb_settings add column if not exists table_calls_enabled boolean not null default false;
alter table kb_settings add column if not exists table_orders_enabled boolean not null default false;
alter table kb_settings add column if not exists call_escalate_minutes integer not null default 3;

create table if not exists kb_table_calls (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    tag_id       uuid not null,
    kind         text not null,                        -- waiter | bill | machine
    status       text not null default 'open',         -- open | accepted | done
    caller_hash  text,                                 -- the browser that asked (a random cookie, hashed)
    staff_id     text,                                 -- who was told first
    accepted_by  text,
    accepted_at  timestamptz,
    escalated_at timestamptz,                          -- a manager was told nobody had answered
    done_at      timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_kb_table_calls_live on kb_table_calls (tenant_id, status, created_at);
create unique index if not exists uq_kb_table_calls_live on kb_table_calls (tag_id, kind)
    where status in ('open', 'accepted');
alter table kb_table_calls enable row level security;
drop policy if exists "tenant_isolation" on kb_table_calls;
create policy "tenant_isolation" on kb_table_calls
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists kb_table_orders (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    tag_id       uuid not null,
    status       text not null default 'pending',      -- pending | confirmed | rejected
    items        jsonb not null,                       -- [{product_id, name, quantity, unit_cents, line_cents}] priced by the server
    total_cents  bigint not null check (total_cents >= 0),
    note         text,
    caller_hash  text,
    staff_id     text,                                 -- who was told
    decided_by   text,
    decided_at   timestamptz,
    reject_reason text,
    bill_id      uuid,                                 -- the table bill its lines went onto
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_kb_table_orders_live on kb_table_orders (tenant_id, status, created_at);
create index if not exists idx_kb_table_orders_tag on kb_table_orders (tenant_id, tag_id, created_at);
alter table kb_table_orders enable row level security;
drop policy if exists "tenant_isolation" on kb_table_orders;
create policy "tenant_isolation" on kb_table_orders
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table kb_table_orders is
    'An order a customer sent from a table tag. Nothing is owed until a waiter confirms it onto the table''s bill.';

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('300_table_service.sql', 300, '3ee89aa4a21aba3b04c05f17e1331c2e66b9a9ea914a0dc17e98c306323a4305', 'sql-editor', false) on conflict do nothing;

commit;

select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name in ('commerce_events','commerce_ticket_types','commerce_tickets','commerce_table_settings','commerce_table_bookings','commerce_specials','kb_table_calls','kb_table_orders');
