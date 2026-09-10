-- ============================================================================
-- Vula — combined catch-up migration, 2026-09-10
--
-- Paste this WHOLE file into the Supabase SQL editor for the DIGG project and run it once.
-- Every statement is idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS), so it is safe to run
-- even if some of these migrations were already applied — re-running changes nothing.
--
-- Bundles migrations 071, 128, 149, 150, 151, 152, 153, 154, 155, 156, 157. After it runs and
-- the backend redeploys, the boot log line "schema check OK — all N migration sentinels present"
-- confirms it took.
-- ============================================================================


-- ── 071 · durable cross-worker dedup for inbound WhatsApp messages ───────────
-- Without this, a Meta webhook retry landing on the other uvicorn worker is processed again
-- (double replies). The primary key makes the "have we seen this message id" check atomic.

create table if not exists vula_wa_msg_dedup (
    msg_id  text primary key,
    seen_at timestamptz not null default now()
);
create index if not exists idx_wa_dedup_seen on vula_wa_msg_dedup (seen_at);

alter table vula_wa_msg_dedup enable row level security;
drop policy if exists vula_wa_msg_dedup_service on vula_wa_msg_dedup;
create policy vula_wa_msg_dedup_service on vula_wa_msg_dedup
    for all to service_role using (true) with check (true);


-- ── 128 · storefront header layout settings ─────────────────────────────────

alter table commerce_invoice_settings
    add column if not exists header_sticky       boolean default true,
    add column if not exists header_nav_position text default 'right',
    add column if not exists header_cta_text     text,
    add column if not exists header_cta_link     text;


-- ── 149 · escalation: record that we came back to the customer ──────────────

alter table vula_escalations
    add column if not exists customer_notified_at timestamptz;
create index if not exists idx_escalations_unnotified
    on vula_escalations (tenant_id, created_at)
    where answered_at is null and customer_notified_at is null;


-- ── 150 · learned answers must be owner-reviewed before they're served ──────

alter table vula_learned_answers
    add column if not exists status      text not null default 'pending',
    add column if not exists approved_at timestamptz,
    add column if not exists approved_by text;
create index if not exists idx_learned_answers_approved
    on vula_learned_answers (tenant_id, created_at desc)
    where status = 'approved';


-- ── 151 · authenticate the inbound ClickUp webhook ─────────────────────────

alter table vula_clickup_accounts
    add column if not exists webhook_secret text,
    add column if not exists webhook_id     text;


-- ── 152 · standing business rules the owner states on WhatsApp ─────────────

create table if not exists vula_business_rules (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    rule        text not null,
    topic       text,
    status      text not null default 'active',
    created_by  text,
    source      text not null default 'whatsapp',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_business_rules_active
    on vula_business_rules (tenant_id, created_at)
    where status = 'active';

alter table vula_business_rules enable row level security;
drop policy if exists business_rules_service_role on vula_business_rules;
create policy business_rules_service_role on vula_business_rules
    for all to service_role using (true) with check (true);


-- ── 153 · WhatsApp outbound delivery tracking ─────────────────────────────

create table if not exists vula_wa_outbound (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    wamid        text not null,
    to_phone     text not null,
    kind         text not null default 'text',
    body_preview text,
    status       text not null default 'accepted',
    error        text,
    notified_at  timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create unique index if not exists vula_wa_outbound_wamid_idx on vula_wa_outbound (wamid);
create index if not exists vula_wa_outbound_tenant_idx on vula_wa_outbound (tenant_id, created_at desc);
create index if not exists vula_wa_outbound_failed_idx
    on vula_wa_outbound (tenant_id)
    where status = 'failed' and notified_at is null;

alter table vula_wa_outbound enable row level security;
drop policy if exists vula_wa_outbound_service on vula_wa_outbound;
create policy vula_wa_outbound_service on vula_wa_outbound
    for all to service_role using (true) with check (true);


-- ── 154 · per-merchant researched allocation ──────────────────────────────

create table if not exists commerce_merchant_profiles (
    tenant_id      text not null,
    merchant_key   text not null,
    display_name   text,
    what_they_sell text,
    account_code   text,
    confidence     text,
    decided_by     text,
    asked_at       timestamptz,
    researched_at  timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    primary key (tenant_id, merchant_key)
);
create index if not exists commerce_merchant_profiles_ask_idx
    on commerce_merchant_profiles (tenant_id)
    where confidence = 'ambiguous' and asked_at is null;

alter table commerce_merchant_profiles enable row level security;
drop policy if exists commerce_merchant_profiles_service on commerce_merchant_profiles;
create policy commerce_merchant_profiles_service on commerce_merchant_profiles
    for all to service_role using (true) with check (true);


-- ── 155 · bank transaction: when was this row marked 'asked' ──────────────

alter table commerce_bank_transactions
    add column if not exists asked_at timestamptz;


-- ── 156 · distributor stock sheets, read as rows ─────────────────────────

create table if not exists vula_stock_sheets (
    tenant_id       text not null,
    doc_id          text not null,
    filename        text,
    as_at           text,
    rows            jsonb not null default '[]'::jsonb,
    product_ranges  jsonb not null default '[]'::jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    primary key (tenant_id, doc_id)
);
create index if not exists vula_stock_sheets_recent_idx
    on vula_stock_sheets (tenant_id, updated_at desc);

alter table vula_stock_sheets enable row level security;
drop policy if exists vula_stock_sheets_service on vula_stock_sheets;
create policy vula_stock_sheets_service on vula_stock_sheets
    for all to service_role using (true) with check (true);


-- ── 157 · one inbound file = one ack = one run (the 8× duplication fix) ───

create table if not exists vula_media_dedup (
    tenant_id      text not null,
    content_sha    text not null,
    kind           text,
    claimed_by     text,
    created_at     timestamptz not null default now(),
    primary key (tenant_id, content_sha)
);
create index if not exists vula_media_dedup_age_idx on vula_media_dedup (created_at);

alter table vula_media_dedup enable row level security;
drop policy if exists vula_media_dedup_service on vula_media_dedup;
create policy vula_media_dedup_service on vula_media_dedup
    for all to service_role using (true) with check (true);


-- ============================================================================
-- Done. Verify: after the next deploy, Railway logs should show
--   schema check OK — all N migration sentinels present
-- ============================================================================
