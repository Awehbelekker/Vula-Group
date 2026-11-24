-- 300_table_service.sql — service at the table (vula/tap/tableside.py, 24 Nov 2026).
--
-- A tag on a restaurant table (kb_tags.bound_type 'table', label "Table 7", bound_id = the waiter
-- serving it now). From the page the tag opens a customer can call the waiter, ask for the bill or
-- the card machine (kb_table_calls), and order from the menu (kb_table_orders). The waiter accepts
-- a call and confirms an order in the staff app; a confirmed order's lines go onto the table's bill
-- (kb_bills.bill_type 'table', lines in kb_tab_items), which the customer pays by tapping the tag —
-- the payment flow that already exists.
--
-- Idempotent. RLS on every table.

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
-- one live call of each kind per table: asking twice is the same request
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
