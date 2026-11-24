-- 301_lesson_hire_requests.sql — lessons and gear hire that the business confirms by hand
-- (vula/commerce/lessons.py, 24 Nov 2026 — surf schools, kite schools, board and wetsuit hire).
--
-- A customer asks for a lesson or for gear in the business's WhatsApp chat: which lesson, their
-- level, group or private, how many people, one session or a package, which day. Vula quotes from
-- the business's own price list (integer cents, worked out in code) and keeps the request. Nothing
-- is booked until a person at the business confirms it with a time and a coach — the weather, the
-- tide and who is teaching that day decide when a lesson runs, so no slot is promised by a machine.
--
-- Idempotent. RLS on every table.

create table if not exists commerce_lesson_settings (
    tenant_id   text primary key,
    enabled     boolean not null default false,
    lessons     jsonb not null default '[]'::jsonb,   -- [{id, name, minutes, max_per_coach, package_sessions,
                                                      --   group:[{min, max, single_cents, package_cents}],   per person
                                                      --   private:{single_cents, package_cents}, extras:[{id, name, cents}]}]
    hire        jsonb not null default '[]'::jsonb,   -- [{id, name, prices:{"2h","4h","1d","2d","3d"}, extra_day_cents}]
    spot        jsonb,                                -- {name, lat, lng}: where lessons run (the conditions check)
    form_url    text,                                 -- the business's own indemnity form, sent with a confirmation
    pay_note    text,                                 -- how to pay, in the business's own words
    note        text,
    updated_at  timestamptz not null default now()
);
alter table commerce_lesson_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_lesson_settings;
create policy "tenant_isolation" on commerce_lesson_settings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_lesson_requests (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    ref             text not null,                        -- what the customer quotes: L-4F7KQ
    kind            text not null check (kind in ('lesson', 'hire')),
    item_id         text not null,                        -- the lesson's or the gear's id in the settings
    item_name       text not null,                        -- as it was called when asked for
    level           text,                                 -- first_timer | beginner | intermediate | advanced
    format          text,                                 -- group | private (lessons)
    sessions        integer not null default 1 check (sessions between 1 and 20),
    party_size      integer not null default 1 check (party_size between 1 and 50),  -- people, or how many of the gear
    duration        text,                                 -- hire: 2h | 4h | 1d | 2d | 3d | 7d
    extras          jsonb not null default '[]'::jsonb,   -- [{id, name, quantity, cents}]
    wanted_date     date not null,
    wanted_part     text not null default 'any',          -- morning | midday | afternoon | any
    customer_name   text not null,
    customer_phone  text,
    notes           text,
    quote_cents     integer check (quote_cents is null or quote_cents >= 0),  -- null: the business prices it
    quote_lines     jsonb not null default '[]'::jsonb,   -- [{description, quantity, unit_price_cents}]
    price_cents     integer check (price_cents is null or price_cents >= 0),  -- what was agreed at confirmation
    status          text not null default 'requested',    -- requested | confirmed | declined | cancelled | completed | no_show
    starts_at       timestamptz,                          -- set when confirmed
    coach_id        text,                                 -- vula_team_members.id
    coach_name      text,
    invoice_id      uuid,                                 -- the draft invoice made at confirmation
    decline_reason  text,
    channel         text not null default 'whatsapp',     -- whatsapp | dashboard
    confirmed_by    text,
    confirmed_at    timestamptz,
    reminded_at     timestamptz,
    conditions_at   timestamptz,                          -- when the evening-before conditions check went out
    conditions      jsonb,                                -- what the forecast said then
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (tenant_id, ref)
);
create index if not exists idx_lesson_requests_status on commerce_lesson_requests (tenant_id, status, wanted_date);
create index if not exists idx_lesson_requests_start on commerce_lesson_requests (tenant_id, starts_at);
create index if not exists idx_lesson_requests_phone on commerce_lesson_requests (tenant_id, customer_phone);
alter table commerce_lesson_requests enable row level security;
drop policy if exists "tenant_isolation" on commerce_lesson_requests;
create policy "tenant_isolation" on commerce_lesson_requests
    using (tenant_id = current_setting('app.tenant_id', true));
