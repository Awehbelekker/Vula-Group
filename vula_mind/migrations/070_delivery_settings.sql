-- 070_delivery_settings.sql — per-tenant delivery coverage + fee rules on commerce_order_settings.
-- Prompted by a live incident (2026-07-16): with no grounded delivery facts, the assistant
-- confidently hallucinated "Ja, ons lewer na Timbuktu" to a customer instead of escalating.
-- These fields are injected into the commerce assistant's system prompt as hard facts, and the
-- fee rules are applied at order-creation time. Editable via the existing
-- GET/PUT /{tenant}/admin/order-settings endpoints (whitelisted in order_workflow._FIELDS).

alter table commerce_order_settings
    add column if not exists delivery_areas jsonb,                -- ["Table View","Blouberg",...]; NULL = not configured (bot must check with team)
    add column if not exists delivery_fee_cents integer,          -- standard fee; NULL = existing default (R80)
    add column if not exists free_delivery_over_cents integer,    -- order subtotal at/above which delivery is free; NULL = never
    add column if not exists min_order_cents integer;             -- minimum order subtotal accepted; NULL = no minimum
