-- 137_automation_firings.sql — approval queue for the existing automations engine
-- (commerce_automations, migration 079). Today a matched automation fires its WhatsApp action
-- immediately (vula/commerce/automations.py::_run_action) — the one real place in this platform
-- where an AI-adjacent trigger acts on a customer with no human in the loop. This closes that
-- gap: a match now STAGES a pending firing here instead of sending anything; the owner reviews
-- and approves/rejects from the dashboard (or WhatsApp), matching the propose-confirm pattern
-- used everywhere else this platform automates something (filing rules, voice-profile
-- suggestions, the confirm=true tool gate). Also the staging surface the new conversational
-- rule-authoring ("teaching mode") relies on — a rule an owner describes in plain language is
-- just another row in commerce_automations, so it inherits this same never-auto-execute gate
-- for free.
create table if not exists commerce_automation_firings (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         text not null,
    automation_id     uuid not null references commerce_automations(id) on delete cascade,
    trigger_context   jsonb not null default '{}'::jsonb,   -- e.g. {order_id, customer_name, status}
    action_type       text not null,                        -- snapshot from the automation at fire time
    action_config     jsonb not null default '{}'::jsonb,
    message           text,                                 -- the filled-in message, ready to send once approved
    status            text not null default 'pending',       -- pending | approved | rejected
    created_at        timestamptz not null default now(),
    decided_at        timestamptz
);
create index if not exists idx_automation_firings_tenant on commerce_automation_firings (tenant_id, status, created_at desc);

alter table commerce_automation_firings enable row level security;
drop policy if exists "tenant_isolation" on commerce_automation_firings;
create policy "tenant_isolation" on commerce_automation_firings
    using (tenant_id = current_setting('app.tenant_id', true));

-- created_from: where this rule came from — "dashboard" (the existing form builder) or
-- "conversation" (the new NL→structured-rule parser, WhatsApp or dashboard chat). Purely
-- informational; doesn't affect how the rule evaluates or fires.
alter table commerce_automations add column if not exists created_from text not null default 'dashboard';
