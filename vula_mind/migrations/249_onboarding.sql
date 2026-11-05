-- 249_onboarding.sql — a new business set up over WhatsApp, from Vula's own number.
--
-- 5 Nov 2026: a test business made in Master was an empty shell — no owner number, no
-- WhatsApp line, no details, no welcome — and Vula had no number of its own to greet them from.
-- Now Master takes the owner's name and WhatsApp number, Vula's own number sends a welcome, and
-- the owner answers the setup questions one at a time on WhatsApp (vula/onboard_wa.py). Until
-- their own number is connected, they work with Vula through Vula's number.
--
--   status   invited (welcome sent, no reply yet) | in_progress | paused (STOP) | done
--   step     the question being asked now (a key in onboard_wa.STEPS)
--   answers  what was saved for each step, as read back to the owner
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists vula_onboarding (
    tenant_id        text primary key,
    owner_phone      text not null,
    owner_name       text,
    owner_email      text,
    status           text not null default 'invited'
                     check (status in ('invited', 'in_progress', 'paused', 'done')),
    step             text,
    answers          jsonb not null default '{}',
    welcome_status   text,
    created_by       text,
    invited_at       timestamptz not null default now(),
    last_message_at  timestamptz,
    reminded_at      timestamptz,
    completed_at     timestamptz,
    updated_at       timestamptz not null default now()
);
create index if not exists idx_onboarding_phone on vula_onboarding (owner_phone);
alter table vula_onboarding enable row level security;
