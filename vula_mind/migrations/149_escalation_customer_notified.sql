-- 149_escalation_customer_notified.sql — close the loop with the CUSTOMER.
--
-- The helper side of the escalate-and-learn loop works: confirmed against real production data
-- (2026-09-01), every escalation raised since the stale-nudge shipped on 2026-07-28 was chased
-- to its assigned helper. The customer side was never closed.
--
-- Real case: an off-the-hook customer asked on 2026-08-25 whether they could collect 100kg of
-- hake instead of having it delivered. Staci was nudged on the 26th, never answered, and the
-- customer has heard nothing since — a real buying question met with permanent silence.
--
-- This column records that we came back to the customer to say we couldn't find out, so the
-- apology is sent exactly once and never to someone whose question was actually answered.

ALTER TABLE vula_escalations
    ADD COLUMN IF NOT EXISTS customer_notified_at timestamptz;

-- The sweep's query: unanswered, not yet apologised for.
CREATE INDEX IF NOT EXISTS idx_escalations_unnotified
    ON vula_escalations (tenant_id, created_at)
    WHERE answered_at IS NULL AND customer_notified_at IS NULL;
