-- 152_business_rules.sql — remember a standing instruction the owner actually gave.
--
-- Real Gerflor message, 2026-08-28 07:00, from Ian to Vula on WhatsApp:
--
--   "...make sure when pricing that you price with the correct discounts. Per-Square price list
--    all is NETT (No further discounts apply). DT is subject to 7% Trade discount, excluding the
--    Mactile which is NETT. Secondly make sure you price the correct Zone for DT. Per-Square
--    doesn't fall into zones. ... Please check with Michelle before pricing items on SPM and
--    myself on Gerflor until you get the pricing structure."
--
-- That is a complete, unambiguous pricing policy. Vula replied "I was unable to find the correct
-- pricing structure for distributors" and retained NOTHING. Days later, on 2026-08-31, the same
-- class of pricing question got the same empty answer. The owner had already given the answer.
--
-- This is deliberately NOT the KB: a document is retrieved only if a query happens to match it,
-- whereas a standing rule must apply to EVERY relevant answer whether or not the wording lines
-- up. Rules are few, short and always-on, so they are injected into the prompt directly.
--
-- Distinct from vula_learned_answers (one Q->A pair, from a customer escalation) — this is a
-- policy the owner states about how work should be done.

CREATE TABLE IF NOT EXISTS vula_business_rules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   text NOT NULL,
    rule        text NOT NULL,
    topic       text,                       -- e.g. 'pricing', 'delivery', 'discounts'
    status      text NOT NULL DEFAULT 'active',   -- active | archived
    created_by  text,                       -- phone number that stated it
    source      text NOT NULL DEFAULT 'whatsapp',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- The only read path: this tenant's active rules, oldest first so the set reads consistently.
CREATE INDEX IF NOT EXISTS idx_business_rules_active
    ON vula_business_rules (tenant_id, created_at)
    WHERE status = 'active';

ALTER TABLE vula_business_rules ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS business_rules_service_role ON vula_business_rules;
CREATE POLICY business_rules_service_role ON vula_business_rules
    FOR ALL TO service_role USING (true) WITH CHECK (true);
