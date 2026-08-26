-- 138_call_sheet.sql — weekly per-rep "call sheet" digest.
--
-- A sales rep logs meetings over WhatsApp (log_meeting); this rolls their own logged meetings
-- up into a persistent, editable document and emails/WhatsApps it to one fixed recipient on a
-- schedule the rep configures. Scoped to the REP (vula_team_members), not the tenant —
-- commerce_scheduled_job_config's (tenant_id, job_type) granularity has no per-rep dimension,
-- and a tenant can have >1 sales_rep (e.g. Gerflor's Ian and Richard Downing), each with their
-- own recipient/schedule/channel.

alter table vula_team_members
    add column if not exists call_sheet_recipient_email text,
    add column if not exists call_sheet_recipient_phone text,   -- WhatsApp companion-notice target
    add column if not exists call_sheet_channel      text not null default 'email',  -- email | whatsapp | both
    add column if not exists call_sheet_day_of_week  smallint not null default 4,    -- Mon=0..Sun=6, default Friday
    add column if not exists call_sheet_hour         smallint not null default 17,
    add column if not exists call_sheet_minute       smallint not null default 0,
    add column if not exists call_sheet_last_sent_at timestamptz;

-- The persistent, editable document itself. log_meeting appends to whichever row is
-- status='open' for that rep; update_call_sheet lets the rep correct/add to it; the weekly job
-- sends it and flips it to 'sent', at which point the next entry lazily opens a fresh one.
create table if not exists vula_call_sheets (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    rep_whatsapp text not null,
    status       text not null default 'open',   -- open | sent
    entries      jsonb not null default '[]'::jsonb,
    created_at   timestamptz not null default now(),
    sent_at      timestamptz
);

-- Only one OPEN call sheet per rep at a time.
create unique index if not exists idx_call_sheets_one_open
    on vula_call_sheets (tenant_id, rep_whatsapp) where status = 'open';
create index if not exists idx_call_sheets_tenant
    on vula_call_sheets (tenant_id, rep_whatsapp, status);

alter table vula_call_sheets enable row level security;
drop policy if exists "tenant_isolation" on vula_call_sheets;
create policy "tenant_isolation" on vula_call_sheets
    using (tenant_id = current_setting('app.tenant_id', true));
