-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
create table if not exists flow_customer_profiles (
    user_id           uuid primary key,
    name              text,
    phone             text,
    birth_year        integer check (birth_year between 1900 and 2100),
    photo_url         text,
    can_swim          boolean,
    gear              jsonb not null default '{}'::jsonb,
    sports            jsonb not null default '{}'::jsonb,
    emergency_sealed  text,
    medical_sealed    text,
    updated_at        timestamptz not null default now()
);
alter table flow_customer_profiles enable row level security;
comment on table flow_customer_profiles is 'A customer''s own profile in the customer app, across schools. Read by a school only through that customer''s booking with it.';
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('309_flow_customer_profiles.sql', 309, '27812bd4641f33c834645f4cc88d507daa17dc5f467b92bdbd4ff9b553611af2', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name = 'flow_customer_profiles';
