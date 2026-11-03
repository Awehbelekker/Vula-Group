-- 242_production_parity.sql — close the gaps between these files and the live database.
--
-- 3 Nov 2026. The first replay of every migration onto a fresh database (local staging,
-- docs/staging.md) was compared with production, table by table. Three kinds of gap:
--
-- A. Production is open where it should be closed. Six tables had a policy named
--    "service_role_all" that was in fact granted to every role (`TO public USING (true)`):
--    anyone holding the public anon key (it ships in the dashboard) could read and change
--    tenants, the phone → business routing, PayFast subscription tokens, users, documents and
--    past questions through the Supabase REST API. Vula's backend uses the service key, which
--    bypasses RLS, and no frontend reads these tables, so restricting the policy to
--    service_role changes nothing for Vula and closes the hole.
-- B. Production is missing things the files create. vula_wa_prompt_state (migration 167; the
--    WhatsApp yes/no follow-up state shared between workers) and 11 indexes were recorded as
--    applied when production was baselined but don't exist there.
-- C. The files are missing things production has, added by hand or from the old
--    vula_mind/supabase/migrations folder. A fresh database lacked them; production already has
--    every one, so on production each statement here is a no-op.
--
-- Idempotent. Safe to re-run.

-- ── A. Service-role-only policies (the only part that changes production behaviour) ─────────

do $$
declare t text;
begin
    foreach t in array array['vula_tenants', 'vula_tenant_phones', 'vula_subscriptions',
                             'vula_users', 'vula_documents', 'vula_queries'] loop
        if to_regclass('public.' || t) is not null then
            execute format('alter table %I enable row level security', t);
            execute format('drop policy if exists "service_role_all" on %I', t);
            execute format('create policy "service_role_all" on %I for all to service_role '
                           'using (true) with check (true)', t);
        end if;
    end loop;
end $$;

-- ── B. Missing on production ──────────────────────────────────────────────────────────────

create table if not exists vula_wa_prompt_state (
    phone       text not null,
    kind        text not null check (kind in ('purpose', 'signature')),
    prompted_at timestamptz not null default now(),
    primary key (phone, kind)
);
create index if not exists idx_wa_prompt_state_prompted_at on vula_wa_prompt_state (prompted_at);
alter table vula_wa_prompt_state enable row level security;
-- No policy, as in 167: service-role only.

create index if not exists idx_approval_steps_approval  on vula_approval_steps (approval_id);
create index if not exists idx_approvals_tenant         on vula_approvals (tenant_id, status);
create index if not exists idx_clickup_accounts_tenant  on vula_clickup_accounts (tenant_id);
create index if not exists idx_doc_extractions_category on vula_document_extractions (tenant_id, category);
create index if not exists idx_field_assign_contractor  on vula_field_assignments (contractor_id);
create index if not exists idx_field_clickup_map_tenant on vula_field_clickup_map (tenant_id);
create index if not exists idx_field_contractors_tenant on vula_field_contractors (tenant_id);
create index if not exists idx_field_signoffs_task      on vula_field_sign_offs (task_id);
create index if not exists idx_field_tasks_project      on vula_field_tasks (project_id);
create index if not exists idx_field_tasks_tenant       on vula_field_tasks (tenant_id);
create index if not exists idx_tenant_users_tenant      on vula_tenant_users (tenant_id);

-- ── C. Missing from the files (already on production) ────────────────────────────────────

alter table commerce_invoices add column if not exists payment_terms_days integer default 30;
alter table commerce_invoices alter column direction drop not null;
alter table commerce_invoices alter column source drop not null;

alter table commerce_orders
    add column if not exists followup_sent_at    timestamptz,
    add column if not exists review_rating       integer,
    add column if not exists review_requested_at timestamptz;

alter table commerce_products
    add column if not exists product_type text not null default 'single'
        check (product_type in ('single', 'bundle')),
    add column if not exists bundle_items jsonb not null default '[]'::jsonb,
    add column if not exists cooking_tips text,
    add column if not exists related_ids  jsonb not null default '[]'::jsonb;

create table if not exists commerce_reviews (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    product_id     uuid,
    order_id       uuid,
    customer_phone text,
    customer_name  text,
    rating         integer not null check (rating between 1 and 5),
    comment        text,
    source         text not null default 'whatsapp',
    created_at     timestamptz not null default now()
);
create index if not exists idx_reviews_tenant  on commerce_reviews (tenant_id, created_at desc);
create index if not exists idx_reviews_product on commerce_reviews (tenant_id, product_id);
alter table commerce_reviews enable row level security;
drop policy if exists "tenant_isolation_reviews" on commerce_reviews;
create policy "tenant_isolation_reviews" on commerce_reviews
    using (tenant_id = (current_setting('request.jwt.claims', true)::json ->> 'tenant_id'));

create table if not exists vula_queries (
    id         bigserial primary key,
    tenant_id  text not null references vula_tenants(tenant_id) on delete cascade,
    phone      text,
    question   text not null,
    answer     text,
    sources    jsonb default '[]'::jsonb,
    channel    text default 'whatsapp',
    created_at timestamptz default now()
);
create index if not exists idx_queries_tenant on vula_queries (tenant_id);

create table if not exists vula_subscriptions (
    id                 bigserial primary key,
    tenant_id          text not null references vula_tenants(tenant_id) on delete cascade,
    plan               text not null,
    status             text not null default 'trial',
    amount_cents       integer,
    payfast_token      text,
    trial_ends         timestamptz,
    current_period_end timestamptz,
    created_at         timestamptz default now(),
    updated_at         timestamptz default now()
);
drop trigger if exists trg_subscriptions_updated_at on vula_subscriptions;
create trigger trg_subscriptions_updated_at
    before update on vula_subscriptions
    for each row execute function update_updated_at();

create table if not exists vula_users (
    id         bigserial primary key,
    tenant_id  text not null references vula_tenants(tenant_id) on delete cascade,
    email      text not null,
    name       text,
    role       text not null default 'member',
    created_at timestamptz default now(),
    unique (tenant_id, email)
);

create index if not exists idx_tenant_phones_phone     on vula_tenant_phones (phone);
create index if not exists idx_tenant_phones_tenant_id on vula_tenant_phones (tenant_id);

-- The tables created above: RLS on, service-role only (the same policy part A sets).
alter table vula_queries       enable row level security;
alter table vula_subscriptions enable row level security;
alter table vula_users         enable row level security;
alter table vula_tenant_phones enable row level security;
drop policy if exists "service_role_all" on vula_queries;
create policy "service_role_all" on vula_queries
    for all to service_role using (true) with check (true);
drop policy if exists "service_role_all" on vula_subscriptions;
create policy "service_role_all" on vula_subscriptions
    for all to service_role using (true) with check (true);
drop policy if exists "service_role_all" on vula_users;
create policy "service_role_all" on vula_users
    for all to service_role using (true) with check (true);
drop policy if exists "service_role_all" on vula_tenant_phones;
create policy "service_role_all" on vula_tenant_phones
    for all to service_role using (true) with check (true);

drop policy if exists "vula_wa_msg_dedup_service" on vula_wa_msg_dedup;
create policy "vula_wa_msg_dedup_service" on vula_wa_msg_dedup
    for all to service_role using (true) with check (true);
