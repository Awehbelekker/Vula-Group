-- 226_dashboard_push.sql — the dashboard's notifications on a phone or computer (2026-10-27, PWA
-- pass 3). Until now only the Tap to Pay coach app could push (kb_push_subs, migration 202, tied
-- to its own enrolled devices); the dashboard told people things only on WhatsApp.
--
-- One row per device that turned notifications on (vula/dash_push.py). A device belongs to the
-- signed-in person: member_phone (digits) is their team member's WhatsApp, so it gets exactly the
-- alerts that person already gets on WhatsApp (vula_team_members.notify, managed in Team or by a
-- WhatsApp "stop/start" message) — vula/integrations/notify.py sends both. A login with no team
-- member row keeps role, for the owner fallback on urgent alerts.
--
-- The push itself is encrypted to the device (Web Push); no message text is stored here.
-- RLS on, like every table. Idempotent.

create table if not exists vula_dash_push_subs (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    endpoint      text not null unique,
    p256dh        text not null,
    auth          text not null,
    member_id     text,
    member_phone  text,
    role          text,
    user_email    text,
    user_agent    text,
    created_at    timestamptz not null default now(),
    last_sent_at  timestamptz,
    failures      int not null default 0
);
create index if not exists idx_dash_push_tenant on vula_dash_push_subs (tenant_id);

alter table vula_dash_push_subs enable row level security;
drop policy if exists "tenant_isolation" on vula_dash_push_subs;
create policy "tenant_isolation" on vula_dash_push_subs using (tenant_id = current_setting('app.tenant_id', true));
