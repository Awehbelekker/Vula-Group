-- 079_automations.sql — a lean trigger → action rules engine (P3, "automations builder").
-- v1 scope, deliberately narrow: two triggers (order reaches a status / product hits its
-- reorder threshold — reusing the P3.3 fields) and two actions (WhatsApp the customer / WhatsApp
-- the team helper), evaluated by a poller (not per-write hooks) so it needed zero changes to the
-- many existing order/stock write paths.

create table if not exists commerce_automations (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    name text not null,
    trigger_type text not null,          -- order_status | low_stock
    trigger_config jsonb not null default '{}',
    action_type text not null,           -- whatsapp_customer | whatsapp_team
    action_config jsonb not null default '{}',
    enabled boolean not null default true,
    last_fired_at timestamptz,
    fire_count integer not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_automations_tenant on commerce_automations(tenant_id);

-- One row per (automation, entity) firing — the dedup ledger so a poller-based engine never
-- double-fires on the same order/product.
create table if not exists commerce_automation_log (
    id uuid primary key default gen_random_uuid(),
    automation_id uuid not null,
    entity_key text not null,
    fired_at timestamptz not null default now(),
    unique (automation_id, entity_key)
);
