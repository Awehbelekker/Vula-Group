-- 209_shop_events.sql — what happens in a WhatsApp shop, for the funnel on Home.
--
-- 2026-10-13 (the WhatsApp shop plan): orders and payments are already rows, but how many
-- people opened the shop, filled a basket, sent it to WhatsApp, or searched for something the
-- shop doesn't sell were nowhere. One small row per event — no phone number, no message text:
-- `term` is only a short product search ("round basin"), lower-cased. Written best-effort by
-- vula/commerce/shop_stats.py (a missing table never breaks the shop). Idempotent. RLS on.

create table if not exists commerce_shop_events (
    id          bigserial primary key,
    tenant_id   text not null,
    kind        text not null check (kind in ('shop_open', 'basket', 'basket_claimed', 'search', 'search_miss')),
    term        text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_commerce_shop_events_tenant on commerce_shop_events (tenant_id, created_at desc);
alter table commerce_shop_events enable row level security;
