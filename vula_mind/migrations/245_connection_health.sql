-- 245_connection_health.sql — the last known state of each business's app connections.
--
-- 5 Nov 2026: connections broke without anyone hearing about it — a mailbox whose saved password
-- couldn't be opened for two weeks, a ClickUp link dead since August, a sign-in button that opened
-- a window that never came back. A daily check (vula/connections.py) works out each connection's
-- state; this table remembers the last one so the owner is told once when it breaks (with a link
-- to reconnect) and once when it works again, not every day.
--
--   provider     clickup | google | microsoft | dynamics365 | email | whatsapp
--   account_key  which one, when a business can have several (a mailbox address, a phone number)
--   state        working | broken | not_connected | unavailable
--   problem      the plain-words reason when broken
--   since        when it entered this state
--   alerted_at   when the owner was told about this break (null = not told yet)
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists vula_connection_health (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    provider        text not null,
    account_key     text not null default '',
    state           text not null check (state in ('working', 'broken', 'not_connected', 'unavailable')),
    problem         text,
    since           timestamptz not null default now(),
    last_checked_at timestamptz not null default now(),
    alerted_at      timestamptz
);
create unique index if not exists idx_connection_health_key
    on vula_connection_health (tenant_id, provider, account_key);
alter table vula_connection_health enable row level security;
