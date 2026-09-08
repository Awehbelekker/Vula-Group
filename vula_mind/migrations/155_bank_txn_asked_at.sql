-- 2026-09-08: commerce_bank_transactions had no way to tell WHEN a row was marked 'asked' —
-- handle_client_answer (vula/commerce/bank_review.py) had to guess which of two independently
-- pending questions (a supplier proof-of-payment asking about money OUT, an unmatched credit
-- asking about money IN) a free-text WhatsApp reply was actually meant for, and always guessed
-- the money-out one regardless of which was actually asked more recently. asked_at lets it pick
-- whichever question was genuinely asked last.
alter table commerce_bank_transactions add column if not exists asked_at timestamptz;
