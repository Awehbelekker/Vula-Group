-- 308_lesson_ratings.sql — a customer rates a lesson once it is done
-- (vula/commerce/lesson_ratings.py, 25 Nov 2026).
--
-- One row per finished lesson, written by the customer who had it (opened by the request's own
-- signed token). Stars for the school, and stars for the coach when the lesson had one. The comment
-- is for the school only: it is never shown to other customers. The averages are worked out from
-- these rows when they are shown, never stored.
--
-- Idempotent.

create table if not exists commerce_lesson_ratings (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    request_id   uuid not null unique,                    -- commerce_lesson_requests.id: one rating a lesson
    stars        integer not null check (stars between 1 and 5),          -- the school
    coach_id     text,                                    -- vula_team_members.id of the lesson's coach
    coach_stars  integer check (coach_stars between 1 and 5),
    comment      text,                                    -- for the school only
    created_at   timestamptz not null default now()
);
create index if not exists idx_lesson_ratings_tenant on commerce_lesson_ratings (tenant_id, created_at desc);

alter table commerce_lesson_ratings enable row level security;
drop policy if exists "tenant_isolation" on commerce_lesson_ratings;
create policy "tenant_isolation" on commerce_lesson_ratings
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_lesson_ratings is 'A customer''s stars for a finished lesson: the school, and its coach. The comment is for the school only.';
