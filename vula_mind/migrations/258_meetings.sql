-- 258_meetings.sql — a team member's own meetings, booked on WhatsApp (12 Nov 2026).
--
-- "Meeting with Acme Builders Thursday 10am at 12 Main Road" → one row. Before it, the person who
-- booked it gets a WhatsApp reminder ONLY if they asked for one ("remind me 1 hour before"), with
-- the address and a Google Maps link (vula/meetings.py, commerce_admin "meeting" tool). Not a
-- customer booking for a service (commerce_bookings) and not meeting notes (log_meeting).
--
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

create table if not exists vula_meetings (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    created_by       text not null,                 -- the WhatsApp number of the person it's for
    with_name        text not null,                 -- who / which company the meeting is with
    contact_phone    text,                          -- commerce_contacts.phone when matched
    address          text,
    notes            text,
    starts_at        timestamptz not null,
    remind_minutes   integer check (remind_minutes is null or remind_minutes between 5 and 2880),
    reminded_at      timestamptz,
    status           text not null default 'scheduled' check (status in ('scheduled', 'cancelled', 'done')),
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
comment on column vula_meetings.remind_minutes is
    'Minutes before starts_at to remind the person — null means no reminder (only if they asked)';

create index if not exists vula_meetings_tenant_person_start on vula_meetings (tenant_id, created_by, starts_at);
create index if not exists vula_meetings_due_reminders on vula_meetings (starts_at)
    where status = 'scheduled' and remind_minutes is not null and reminded_at is null;

alter table vula_meetings enable row level security;
