-- ============================================================
-- Vula Group — Migration 047: contact onboarding state
-- Tracks a contact through the Staci-controlled opt-in / intro campaign:
--   invited -> (template sent) awaiting_reply -> (they reply) collecting -> complete
-- consent flips to opted_in on reply; opted_out on STOP. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_contacts
    add column if not exists onboarding_state text not null default 'invited',
        -- invited | awaiting_reply | collecting | complete | opted_out
    add column if not exists onboarding_at   timestamptz,   -- when the intro template was sent
    add column if not exists consent_at       timestamptz;   -- when they opted in/out

create index if not exists idx_commerce_contacts_onboarding
    on commerce_contacts (tenant_id, onboarding_state);
