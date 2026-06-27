-- 022_segments.sql — saved custom audience segments for broadcasts.
--
-- A segment is a named set of criteria evaluated against the aggregated customer
-- list (orders + WhatsApp sessions). Referenced by broadcasts/campaigns as
-- audience_filter = 'seg:<id>'. Criteria keys (all optional, ANDed):
--   ordered_within_days, not_ordered_within_days, min_spend, max_spend,
--   min_orders, channel (whatsapp|web)

create table if not exists commerce_segments (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    name        text not null,
    criteria    jsonb not null default '{}'::jsonb,
    created_by  text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_commerce_segments_tenant on commerce_segments (tenant_id);
