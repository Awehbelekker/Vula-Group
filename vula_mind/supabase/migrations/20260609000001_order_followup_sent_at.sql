-- Track when we sent the unpaid payment follow-up so we don't chase twice.
ALTER TABLE commerce_orders
  ADD COLUMN IF NOT EXISTS followup_sent_at TIMESTAMPTZ DEFAULT NULL;
