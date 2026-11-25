-- 309_flow_customer_profiles.sql — a customer's own profile in the customer app
-- (vula/commerce/lesson_customers.py, 25 Nov 2026).
--
-- One row per signed-in customer (Supabase Auth user), across every school: who they are, what
-- gear fits them, and for each sport their level and what they can already do. It is the
-- customer's own, so it carries no tenant_id; a school reads it only through a booking that
-- customer made with it. The emergency contact and the medical note are stored encrypted
-- (vula/secret_keys). A coach's confirmed level is kept inside `sports`, beside the customer's own.
--
-- RLS is on with no policy: only the service role (the API) reads or writes it. Idempotent.

create table if not exists flow_customer_profiles (
    user_id           uuid primary key,                   -- auth.users.id
    name              text,
    phone             text,
    birth_year        integer check (birth_year between 1900 and 2100),
    photo_url         text,                               -- their sign-in account's picture, if they keep it
    can_swim          boolean,                            -- 50 m unaided
    gear              jsonb not null default '{}'::jsonb, -- height_cm, weight_kg, wetsuit
    sports            jsonb not null default '{}'::jsonb, -- {sport: {level, skills[], confirmed{level, school, at}}}
    emergency_sealed  text,                               -- encrypted: name and number
    medical_sealed    text,                               -- encrypted: what a coach should know
    updated_at        timestamptz not null default now()
);
alter table flow_customer_profiles enable row level security;
comment on table flow_customer_profiles is 'A customer''s own profile in the customer app, across schools. Read by a school only through that customer''s booking with it.';
