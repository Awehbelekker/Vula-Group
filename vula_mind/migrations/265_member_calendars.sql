-- 265_member_calendars.sql — each team member's own Outlook calendar (13 Nov 2026).
--
-- The business's Microsoft account (vula_microsoft_accounts) is one per business; a rep's own
-- meetings live in their own calendar. A person connects theirs from WhatsApp ("connect my
-- calendar" → a 15-minute link) or the dashboard; Vula reads it (Calendars.Read only — no mail,
-- no files) into their own diary (vula/outlook_calendar.py). Only the person can connect their own.
-- Tokens are Fernet-encrypted like the business account's. The server (service role) is the only
-- reader/writer; RLS on with no policies. Idempotent.

create table if not exists vula_member_calendars (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    member_id        text not null,                  -- vula_team_members.id
    provider         text not null default 'microsoft',
    email            text,
    access_token     text,
    refresh_token    text,
    token_expiry     timestamptz,
    scopes           text,
    status           text not null default 'connected' check (status in ('connected', 'error')),
    last_synced_at   timestamptz,
    last_sync_error  text,
    connected_at     timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
create unique index if not exists vula_member_calendars_one
    on vula_member_calendars (tenant_id, member_id, provider);

alter table vula_member_calendars enable row level security;
