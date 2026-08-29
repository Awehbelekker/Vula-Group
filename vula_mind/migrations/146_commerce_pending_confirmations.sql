-- 146_commerce_pending_confirmations.sql — real WhatsApp reply-button confirm flows.
--
-- Every confirm-gated commerce_admin tool (22 of them: update_stock, record_payment,
-- upsert_supplier, create_purchase_order, create_booking, ...) already returns a consistent
-- {"preview": true, ...} shape when called without confirm=true. Until now, turning that preview
-- into an actual confirmation relied on the model writing a sentence asking "should I go ahead?"
-- and then correctly parsing the owner's free-text "yes"/"confirm"/"proceed" reply back into a
-- second tool call with confirm=true — exactly the ambiguity that produced the fabricated Regan
-- invoice (2026-08-22/24): the model retried blindly, misread its own tool results, and invented
-- a success that never happened.
--
-- This table backs a structural fix: when a tool returns preview:true, the owner gets real
-- WhatsApp reply buttons (Confirm / Cancel) instead of free text. A button tap sends back an
-- exact, unambiguous payload — no LLM interpretation of the reply is needed at all. See
-- core/skills/commerce_admin.py's ConfirmationRequired / _agent_loop, and
-- vula/api/whatsapp.py's _handle_admin_confirm_reply.

create table if not exists commerce_pending_confirmations (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    phone         text not null,
    skill_name    text not null default 'commerce_admin',
    tool_name     text not null,
    tool_args     jsonb not null default '{}'::jsonb,
    summary       text not null,
    status        text not null default 'pending'
                  check (status in ('pending', 'confirmed', 'cancelled', 'expired')),
    created_at    timestamptz not null default now(),
    expires_at    timestamptz not null default (now() + interval '15 minutes'),
    resolved_at   timestamptz
);

create index if not exists idx_pending_confirmations_lookup
    on commerce_pending_confirmations (tenant_id, phone, status);

alter table commerce_pending_confirmations enable row level security;
-- Intentionally no policy (matches migration 115's convention): written/read exclusively by
-- backend code via the service-role key, which bypasses RLS entirely — a permissive policy
-- here would be redundant, not an actual boundary.
