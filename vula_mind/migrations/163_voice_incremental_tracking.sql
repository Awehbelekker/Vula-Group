-- 163_voice_incremental_tracking.sql — Tenant Mind Phase 2: incremental voice learning.
--
-- vula/commerce/voice_profile.py::analyze_voice() already does everything right (real
-- owner-authored text, propose-confirm, never auto-applied) — it just only ever ran when
-- someone manually triggered it (dashboard button, or the WhatsApp admin tool). Nothing
-- re-checked as new real messages/notes/emails accumulated.
--
-- This column is the "how much data did we last actually analyse" marker, so a scheduled
-- re-check can tell "nothing meaningfully new since last time" (skip — no point spending an
-- LLM call) apart from "20 more real messages since then" (worth re-analysing). Nullable =
-- never analysed yet, no behaviour change for any existing tenant.

alter table vula_tenant_config
    add column if not exists voice_last_checked_sample_count integer;
