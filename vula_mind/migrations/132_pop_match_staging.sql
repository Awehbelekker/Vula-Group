-- 132_pop_match_staging.sql — lets a proposed candidate ride along with a pending
-- commerce_bank_transactions row so a WhatsApp "yes" can confirm it directly, instead of the
-- owner having to retype the order/invoice number bank_review.py's existing flow already asks
-- for. Nullable, only ever set for WhatsApp-proof-of-payment-sourced rows — every other
-- reconciliation path (statement import, email POP) leaves both columns null and is unaffected.
alter table commerce_bank_transactions add column if not exists proposed_match_type text;  -- 'invoice' | 'order'
alter table commerce_bank_transactions add column if not exists proposed_match_id uuid;
