-- 298_events_tickets.sql — events and tickets (vula/commerce/events.py, 24 Nov 2026).
--
-- An event has ticket types. Each ticket type is backed by a commerce_products row
-- (product_type 'ticket'), so a ticket sale is an ordinary order: the same basket, the same
-- atomic stock hold (capacity), the same payment gateways, the same books. When the order is
-- paid, one commerce_tickets row is issued per ticket bought, each with its own unguessable code
-- (the QR). A ticket is checked in once, in one conditional write.
--
-- Idempotent. RLS on every table.

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
