-- ============================================================
-- Vula Group — Migration 231: Tap to Pay bar tabs
-- A customer taps the bar's tag and opens a tab under their WhatsApp number (kb_bills row with
-- bill_type='tab', addressed to that number). Staff add drinks to it (kb_tab_items); the customer
-- pays it like any bill, by tapping again or replying PAY TAB. The owner sees the name and number.
--   kb_settings.tabs_enabled     the business offers tabs (off until switched on)
--   kb_settings.tab_idle_hours   a tab with no new item for this long gets a pay prompt + owner alert
--   kb_bills.customer_name       the customer's WhatsApp name, for the staff list
--   kb_bills.last_item_at        when the last item was added (drives the idle prompt)
--   kb_bills.tab_nudged_at       when the idle prompt went out (once per tab)
-- Idempotent. Run in the Supabase SQL editor.
-- ============================================================
alter table kb_settings add column if not exists tabs_enabled boolean not null default false;
alter table kb_settings add column if not exists tab_idle_hours int not null default 3;
alter table kb_bills add column if not exists customer_name text;
alter table kb_bills add column if not exists last_item_at timestamptz;
alter table kb_bills add column if not exists tab_nudged_at timestamptz;

create table if not exists kb_tab_items (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    bill_id     uuid not null references kb_bills(id),
    description text not null,
    quantity    int not null default 1 check (quantity > 0),
    unit_cents  bigint not null check (unit_cents >= 0),
    line_cents  bigint not null check (line_cents >= 0),
    added_by    text,
    voided_at   timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_tab_items_bill on kb_tab_items (tenant_id, bill_id);
create index if not exists idx_kb_bills_tabs on kb_bills (tenant_id, bill_type, status) where bill_type = 'tab';

-- server-side access only, like every other kb_* table
alter table kb_tab_items enable row level security;
