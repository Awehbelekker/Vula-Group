-- 304_beach_reports.sql — what the water looks like right now, from a photo a coach takes on the
-- beach (vula/commerce/beach_reports.py, 24 Nov 2026).
--
-- A team member photographs the water; a vision model reads it into a few fixed words (surface,
-- rough wave size, how busy) which are checked in code; the customer app shows the reading beside
-- the forecast. The photo itself is NOT kept: it may show people, so only the reading is stored.
--
-- Idempotent. RLS on.

create table if not exists commerce_beach_reports (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    spot_name     text,
    taken_at      timestamptz not null default now(),
    reported_by   text,                                  -- vula_team_members.id (or the login) of who took it
    reporter_name text,                                  -- their first name, as shown to customers
    water_visible boolean not null default true,
    surface       text check (surface is null or surface in ('glassy', 'clean', 'bumpy', 'choppy', 'blown_out')),
    wave_size     text check (wave_size is null or wave_size in ('flat', 'knee', 'waist', 'chest', 'head', 'overhead')),
    crowd         text check (crowd is null or crowd in ('empty', 'few', 'busy', 'packed')),
    confidence    text check (confidence is null or confidence in ('low', 'medium', 'high')),
    coach_note    text,                                  -- the coach's own words, shown to customers
    model         text,                                  -- which model read it
    created_at    timestamptz not null default now()
);
create index if not exists idx_beach_reports_recent on commerce_beach_reports (tenant_id, taken_at desc);
alter table commerce_beach_reports enable row level security;
drop policy if exists "tenant_isolation" on commerce_beach_reports;
create policy "tenant_isolation" on commerce_beach_reports
    using (tenant_id = current_setting('app.tenant_id', true));
