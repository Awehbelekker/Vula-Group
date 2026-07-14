-- ============================================================
-- Vula Group — Migration 051: customer product subscriptions (recurring orders)
-- "Fish every Friday." A standing order that auto-creates a real commerce_order on its
-- cadence, so regulars don't have to re-order. Distinct from Vula's own SaaS billing.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_subscriptions (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    customer_name    text,
    customer_phone   text,
    customer_email   text,
    delivery_address text,
    delivery_slot    text,
    items            jsonb not null default '[]'::jsonb,  -- [{product_id,product_name,quantity,unit_price_cents}]
    delivery_cents   int  not null default 0,
    payment_method   text,                                 -- online | cod | eft
    cadence          text not null default 'weekly',       -- weekly | biweekly | monthly
    next_run         date not null,                        -- next date an order is created
    last_run         timestamptz,
    status           text not null default 'active',       -- active | paused | cancelled
    channel          text not null default 'dashboard',
    note             text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
create index if not exists idx_commerce_subs_due on commerce_subscriptions (status, next_run);
create index if not exists idx_commerce_subs_tenant on commerce_subscriptions (tenant_id, status);
create index if not exists idx_commerce_subs_phone on commerce_subscriptions (tenant_id, customer_phone);

alter table commerce_subscriptions enable row level security;
drop policy if exists "tenant_isolation" on commerce_subscriptions;
create policy "tenant_isolation" on commerce_subscriptions
    using (tenant_id = current_setting('app.tenant_id', true));
