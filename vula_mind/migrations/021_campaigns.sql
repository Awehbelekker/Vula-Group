-- 021_campaigns.sql — scheduled + recurring WhatsApp broadcast campaigns.
--
-- A campaign fires admin_send_broadcast at next_run_at. 'once' deactivates after
-- firing; daily/weekly/monthly advance next_run_at. Processed by an in-process loop
-- (and a cron endpoint as backstop). Suppression + delivery analytics apply to each send.

create table if not exists commerce_campaigns (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    name            text not null,
    template_name   text,                          -- Meta template (proactive sends)
    body            text,                          -- free-text (Twilio / 24h window)
    language        text default 'en',
    audience_filter text not null default 'all',   -- all | active_30d | high_value
    recurrence      text not null default 'once',  -- once | daily | weekly | monthly
    next_run_at     timestamptz not null,          -- when to fire next
    active          boolean not null default true,
    last_run_at     timestamptz,
    created_by      text,
    created_at      timestamptz not null default now()
);
create index if not exists idx_commerce_campaigns_due on commerce_campaigns (active, next_run_at);
create index if not exists idx_commerce_campaigns_tenant on commerce_campaigns (tenant_id);
