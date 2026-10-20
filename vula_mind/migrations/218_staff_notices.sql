-- 218_staff_notices.sql — a business's notices to its own staff on WhatsApp (20 Oct 2026, Ian:
-- "broadcasting to the staff, like for birthdays, public holidays, any notifications they need to
-- know about, maybe a newsletter").
--
--   vula_team_members      + birthday (MM-DD only, never the year), whether the team is told,
--                            and the person's own opt-out of birthday/holiday messages (work
--                            notices still reach everyone)
--   vula_staff_notices     one row per notice: what it says, who it's for, must-read or not,
--                            when it goes out — also the dashboard noticeboard
--   vula_staff_notice_recipients
--                          one row per person per notice (unique, so a notice can never reach
--                            the same person twice): delivered / read / "Got it" times
--   vula_tenant_config.staff_notices
--                          the business's settings: birthdays, public holidays, send hour
--
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

alter table vula_team_members add column if not exists birthday text
    check (birthday is null or birthday ~ '^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$');
alter table vula_team_members add column if not exists birthday_share boolean not null default true;
alter table vula_team_members add column if not exists notices_opt_out boolean not null default false;
comment on column vula_team_members.birthday is 'MM-DD only (no year) — for the birthday wish';
comment on column vula_team_members.notices_opt_out is
    'The person asked not to get birthday / public holiday messages; work notices still go';

alter table vula_tenant_config add column if not exists staff_notices jsonb;
comment on column vula_tenant_config.staff_notices is
    '{"birthdays": bool, "holidays": bool, "send_hour": 8, "holiday_note": "..."} — off until set';

create table if not exists vula_staff_notices (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    kind        text not null default 'notice'
                check (kind in ('notice', 'birthday', 'holiday', 'newsletter')),
    title       text not null,
    body        text not null,
    image_url   text,
    must_read   boolean not null default false,
    audience    jsonb not null default '{"all": true}'::jsonb,   -- {"all":true} | {"roles":[..]} | {"ids":[..]}
    send_at     timestamptz,
    status      text not null default 'draft'
                check (status in ('draft', 'scheduled', 'sending', 'sent', 'cancelled')),
    created_by  text,
    sent_at     timestamptz,
    reminded_at timestamptz,
    summary_sent_at timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists idx_staff_notices_tenant on vula_staff_notices (tenant_id, created_at desc);
create index if not exists idx_staff_notices_due on vula_staff_notices (status, send_at)
    where status = 'scheduled';
alter table vula_staff_notices enable row level security;

create table if not exists vula_staff_notice_recipients (
    id              uuid primary key default gen_random_uuid(),
    notice_id       uuid not null references vula_staff_notices(id) on delete cascade,
    tenant_id       text not null,
    member_id       uuid not null,
    name            text,
    phone           text,
    wamid           text,
    sent_at         timestamptz,
    failed          text,
    delivered_at    timestamptz,
    read_at         timestamptz,
    acknowledged_at timestamptz,
    reminded_at     timestamptz,
    created_at      timestamptz not null default now(),
    unique (notice_id, member_id)
);
create index if not exists idx_staff_notice_rcpt_wamid on vula_staff_notice_recipients (wamid)
    where wamid is not null;
create index if not exists idx_staff_notice_rcpt_tenant on vula_staff_notice_recipients (tenant_id, notice_id);
alter table vula_staff_notice_recipients enable row level security;
