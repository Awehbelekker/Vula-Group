-- 259_checkins.sql — a team member checking in at a meeting or site visit (12 Nov 2026).
--
-- "Arrived" (or "I'm at Acme") → a row waiting for the location pin; the pin they then share →
-- checked in, with WhatsApp's own time for the message, the place, and how far it is from the
-- meeting's address (vula/checkins.py). Photos the person sends in the hour after are read from
-- vula_filed_documents (filed_by, image/*) — not copied here. Location is recorded only at a
-- check-in the person makes, never tracked.
--
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

create table if not exists vula_checkins (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    person       text not null,                     -- WhatsApp number, digits
    meeting_id   uuid,                              -- vula_meetings.id when it was for a meeting
    with_hint    text,                              -- "Acme" when there was no meeting to match
    status       text not null default 'awaiting_pin' check (status in ('awaiting_pin', 'checked_in')),
    lat          double precision,
    lng          double precision,
    place        text,                              -- the pin's own name/address, if WhatsApp sent one
    distance_m   integer,                           -- from the meeting address, when it could be placed
    at           timestamptz,                       -- WhatsApp's timestamp on the pin message
    created_at   timestamptz not null default now()
);
create index if not exists vula_checkins_tenant_person on vula_checkins (tenant_id, person, created_at);
create index if not exists vula_checkins_meeting on vula_checkins (meeting_id);

alter table vula_checkins enable row level security;
