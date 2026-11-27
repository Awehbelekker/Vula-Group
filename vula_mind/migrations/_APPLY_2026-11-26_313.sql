-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
alter table commerce_lesson_requests add column if not exists paid_at timestamptz;
alter table commerce_lesson_requests add column if not exists paid_via text;
alter table commerce_lesson_requests add column if not exists tap_bill_id uuid;
create index if not exists idx_lesson_requests_tap_bill on commerce_lesson_requests (tenant_id, tap_bill_id)
    where tap_bill_id is not null;
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('313_lesson_paid_by_tap.sql', 313, 'a9f95c3512105031647c267c431b904227fd784398d0601090d16cd0ad24bc9f', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_columns from information_schema.columns where table_schema = 'public' and table_name = 'commerce_lesson_requests' and column_name in ('paid_at', 'paid_via', 'tap_bill_id');
