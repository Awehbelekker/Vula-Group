-- ============================================================
-- Vula Group — Migration 116: per-tenant persona/voice
-- Vula's assistant already varies its CONTENT per tenant (KB, business-type prompt
-- branching) but not its VOICE — a fish shop and an architecture firm sound like the
-- same assistant with different facts plugged in. This adds one plain-text field the
-- tenant (or Ian, during onboarding) writes directly, e.g. "Warm, casual, quick
-- replies — small family fish shop, not corporate." Nullable/empty = no override,
-- every existing tenant's behaviour is unchanged by default.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_tenant_config add column if not exists persona_prompt text;
