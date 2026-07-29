-- ============================================================
-- Vula Group — Migration 118: owner-configurable WhatsApp conversation flows
-- MVP no-code flow builder: an owner defines trigger phrases, a linear sequence of
-- steps (prompt + expected reply type + keyword branch rules), and a terminal action
-- drawn from the existing commerce_admin tool vocabulary. Closes a real competitive
-- gap (Wati/Zoko/AiSensy/Interakt all have this, Vula didn't). Deliberately NOT a
-- visual node-graph builder — same scoped-down philosophy as automations.py
-- (migration 079), which this mirrors structurally.
--
-- commerce_conversation_sessions (migration 007 + later ALTERs) has no flow-position
-- concept at all — these are genuinely new tables, not an ALTER on an existing one.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_flows (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    name            text not null,
    trigger_phrases jsonb not null default '[]'::jsonb,   -- ["book a demo", "schedule a demo"]
    steps           jsonb not null default '[]'::jsonb,   -- [{prompt, reply_type, options, save_as, branches, default_next}, ...]
    end_action      jsonb not null default '{}'::jsonb,   -- {tool, arg_map, closing_message}
    enabled         boolean not null default true,
    fire_count      integer not null default 0,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists idx_commerce_flows_tenant on commerce_flows (tenant_id, enabled);

alter table commerce_flows enable row level security;
drop policy if exists "tenant_isolation" on commerce_flows;
create policy "tenant_isolation" on commerce_flows
    using (tenant_id = current_setting('app.tenant_id', true));

-- Per-conversation flow position. Only one ACTIVE flow per phone per tenant at a time
-- (partial unique index) -- a customer already mid-flow can't accidentally start a second.
create table if not exists commerce_flow_sessions (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null,
    phone               text not null,                 -- digits-normalized
    flow_id             uuid not null references commerce_flows(id) on delete cascade,
    current_step_index  integer not null default 0,
    collected_answers   jsonb not null default '{}'::jsonb,
    status              text not null default 'active',  -- active | completed | abandoned
    started_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    completed_at        timestamptz
);
create unique index if not exists uq_commerce_flow_sessions_active
    on commerce_flow_sessions (tenant_id, phone) where status = 'active';
create index if not exists idx_commerce_flow_sessions_tenant on commerce_flow_sessions (tenant_id, status);

alter table commerce_flow_sessions enable row level security;
drop policy if exists "tenant_isolation" on commerce_flow_sessions;
create policy "tenant_isolation" on commerce_flow_sessions
    using (tenant_id = current_setting('app.tenant_id', true));
