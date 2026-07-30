-- ============================================================
-- Vula Group — Migration 119: voice-profile suggestions
-- persona_prompt (migration 116) is a static field only Ian could set by hand. This adds a
-- SUGGESTED value Vula can propose after analysing the tenant's own agent-authored WhatsApp
-- replies (commerce_conversation_messages.role='agent') — never auto-applied, owner accepts,
-- edits, or dismisses it via the dashboard. Nullable = no suggestion pending, no behaviour
-- change for any existing tenant.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_tenant_config add column if not exists persona_prompt_suggested text;
alter table vula_tenant_config add column if not exists persona_prompt_suggested_at timestamptz;
