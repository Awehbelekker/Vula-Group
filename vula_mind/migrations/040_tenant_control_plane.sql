-- ============================================================
-- Vula Group — Migration 040: Tenant Control Plane
-- Single source of truth for per-tenant operational config, keyed by the SLUG
-- tenant_id used everywhere else (off-the-hook, digg-demo) — not the vula_tenants UUID.
-- Makes onboarding code-free + industry-aware. Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_tenant_config (
    tenant_id                 text primary key,              -- canonical slug
    display_name              text,
    business_type             text,                          -- food | services | retail | health | trades | other
    store_url                 text,
    domains                   jsonb not null default '[]'::jsonb,
    theme                     jsonb not null default '{}'::jsonb,  -- {accent, accent_dark, ink, logo_url, font}
    default_payment_provider  text,
    wa_phone_number_id        text,
    modules                   jsonb not null default '[]'::jsonb,  -- enabled capability keys
    plan                      text default 'starter',
    status                    text not null default 'active',
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

alter table vula_tenant_config enable row level security;
drop policy if exists "tenant_isolation" on vula_tenant_config;
create policy "tenant_isolation" on vula_tenant_config
    using (tenant_id = current_setting('app.tenant_id', true));

-- DB-driven WhatsApp number routing: which mode a number drives (commerce | knowledge)
alter table vula_whatsapp_accounts add column if not exists route_mode text default 'commerce';

-- ── Seed the live tenants ────────────────────────────────────────────────────
insert into vula_tenant_config
    (tenant_id, display_name, business_type, store_url, theme,
     default_payment_provider, wa_phone_number_id, modules, plan, status)
values
  ('off-the-hook', 'Off the Hook', 'food', 'https://offthehook.co.za',
   '{"accent":"#0E7C7B","ink":"#0B3B3A"}'::jsonb, 'yoco', '1216487374874418',
   '["products","orders","payments","invoices","delivery","crm","reports","broadcasts","inbox","followups","budget","team"]'::jsonb,
   'growth', 'active'),
  ('digg-demo', 'DIGG Architecture', 'services', 'https://digg.vula-ai.com',
   '{"accent":"#1E1E1E","ink":"#1E1E1E"}'::jsonb, null, '1180015145200511',
   '["invoices","projects","documents","finances","fieldops","followups","reports"]'::jsonb,
   'business', 'active'),
  ('awake-sa', 'Awake South Africa', 'retail', 'https://awakesa.co.za',
   '{"accent":"#1B4D3E"}'::jsonb, null, null,
   '["products","orders","payments","invoices","pages","crm","reports"]'::jsonb,
   'growth', 'active')
on conflict (tenant_id) do update set
   display_name = excluded.display_name, business_type = excluded.business_type,
   store_url = excluded.store_url, theme = excluded.theme,
   default_payment_provider = excluded.default_payment_provider,
   wa_phone_number_id = excluded.wa_phone_number_id, modules = excluded.modules,
   plan = excluded.plan, status = excluded.status, updated_at = now();

-- DIGG's number drives the knowledge assistant, not commerce
update vula_whatsapp_accounts set route_mode = 'knowledge' where tenant_id = 'digg-demo';
