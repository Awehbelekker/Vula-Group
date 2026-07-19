-- 069_scheduled_job_config.sql — per-tenant config for the 5 system WhatsApp jobs
-- (delivery_briefing, unpaid_order_chase, low_stock_alert, friday_catch_reminder,
-- sales_summary). Until now their fire times and template names were Python constants in
-- vula/api/server.py; this lets a tenant/master admin change them from the dashboard
-- (⏰ Scheduling tab). A missing row (or NULL field) means "use the built-in default",
-- so existing behaviour is unchanged until someone actually edits a job.

create table if not exists commerce_scheduled_job_config (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    job_type         text not null,   -- delivery_briefing | unpaid_order_chase | low_stock_alert
                                      --   | friday_catch_reminder | sales_summary
    enabled          boolean not null default true,
    template_name    text,            -- NULL = default tenant-prefixed name (e.g. oth_delivery_briefing)
    hour             integer check (hour between 0 and 23),      -- SAST; NULL = job default
    minute           integer check (minute between 0 and 59),    -- NULL = job default
    day_of_week      integer check (day_of_week between 0 and 6),-- 0=Mon..6=Sun, weekly jobs only
    interval_minutes integer check (interval_minutes >= 5),      -- interval jobs only (unpaid chase)
    last_fired_at    timestamptz,     -- set atomically when the scheduler claims a run (dedup)
    updated_at       timestamptz not null default now(),
    unique (tenant_id, job_type)
);

create index if not exists idx_sched_job_tenant on commerce_scheduled_job_config (tenant_id);

alter table commerce_scheduled_job_config enable row level security;
drop policy if exists "tenant_isolation" on commerce_scheduled_job_config;
create policy "tenant_isolation" on commerce_scheduled_job_config
    using (tenant_id = current_setting('app.tenant_id', true));
