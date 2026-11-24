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
