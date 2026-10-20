-- 219_staff_polls.sql — staff notices, part 2 (20 Oct 2026, Ian: "Will Vula auto pick up birthdays
-- and special days? Even voting? Option for anonymous?").
--
--   vula_team_members      + start_date (work anniversaries), birthday_asked_at (Vula asks once)
--   vula_staff_notices     kind also 'poll', 'anniversary', 'special_day'
--   vula_staff_polls       a question with 2–10 choices, anonymous or not, an optional close time
--   vula_staff_poll_voters WHO has voted (for "9 of 12 voted" and the one reminder)
--   vula_staff_poll_votes  WHAT was voted, keyed by an HMAC of poll + person so a vote can be
--                          changed; member_id is only stored when the poll isn't anonymous, so an
--                          anonymous answer can't be joined back to a person through Vula
--
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

alter table vula_team_members add column if not exists start_date date;
alter table vula_team_members add column if not exists birthday_asked_at timestamptz;

alter table vula_staff_notices drop constraint if exists vula_staff_notices_kind_check;
alter table vula_staff_notices add constraint vula_staff_notices_kind_check
    check (kind in ('notice', 'birthday', 'holiday', 'newsletter', 'poll', 'anniversary', 'special_day'));

create table if not exists vula_staff_polls (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    notice_id       uuid references vula_staff_notices(id) on delete cascade,
    question        text not null,
    options         jsonb not null,                 -- ["Fri 5 Dec", "Sat 6 Dec", ...] (2–10)
    anonymous       boolean not null default false,
    closes_at       timestamptz,
    status          text not null default 'open' check (status in ('open', 'closed')),
    reminded_at     timestamptz,
    summary_sent_at timestamptz,
    created_by      text,
    created_at      timestamptz not null default now()
);
create index if not exists idx_staff_polls_tenant on vula_staff_polls (tenant_id, created_at desc);
create index if not exists idx_staff_polls_open on vula_staff_polls (status) where status = 'open';
alter table vula_staff_polls enable row level security;

create table if not exists vula_staff_poll_voters (
    poll_id   uuid not null references vula_staff_polls(id) on delete cascade,
    tenant_id text not null,
    member_id uuid not null,
    voted_at  timestamptz not null default now(),
    primary key (poll_id, member_id)
);
alter table vula_staff_poll_voters enable row level security;

create table if not exists vula_staff_poll_votes (
    id           uuid primary key default gen_random_uuid(),
    poll_id      uuid not null references vula_staff_polls(id) on delete cascade,
    tenant_id    text not null,
    voter_key    text not null,
    option_index integer not null check (option_index between 0 and 9),
    member_id    uuid,                               -- null on an anonymous poll
    updated_at   timestamptz not null default now(),
    unique (poll_id, voter_key)
);
alter table vula_staff_poll_votes enable row level security;
