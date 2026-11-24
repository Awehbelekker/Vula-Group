-- Vula: migration 306 in one paste (24 Nov 2026) — a customer's own account in the customer app.
-- Safe to run more than once. Paste ALL of it into an empty Supabase SQL editor and press Run.
-- One transaction: all of it applies, or none of it. Needs migration 303 (already applied).
begin;

-- ===== 306_lesson_customer_accounts.sql =====
-- 306_lesson_customer_accounts.sql — a customer's own account in the customer app
-- (vula/commerce/lessons.py `for_account`, 24 Nov 2026).
--
-- A customer can sign in (Supabase Auth, e.g. Google) in the customer app. A request made while
-- signed in carries that login's id, so the customer sees their own requests on any phone. A
-- customer's login is nobody's team login: it gets no business and no dashboard. Requests made
-- without signing in are unchanged (the column stays empty).
--
-- Idempotent. No new table: the column sits on a table that already has RLS.

alter table commerce_lesson_requests add column if not exists customer_user_id uuid;
create index if not exists idx_lesson_requests_customer on commerce_lesson_requests (customer_user_id, created_at desc)
    where customer_user_id is not null;

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('306_lesson_customer_accounts.sql', 306, '2e802567fe8917ec0847e700dfbfdc927eafba32dab9681aaa71bfbd01dddc47', 'sql-editor', false) on conflict do nothing;

commit;

select 'done' as result,
       (select count(*) from information_schema.columns where table_schema = 'public' and table_name = 'commerce_lesson_requests' and column_name = 'customer_user_id') as new_columns;
