-- 294_pop_questions.sql — a proof of payment's numbered question and its filed document (22 Nov 2026).
--
-- A proof of payment the business sends on WhatsApp is staged as a row here and asked about. It used to
-- offer one guessed bill ("Reply yes") and settle that one bill with no payment recorded. Now it offers
-- the supplier's bill(s) adding up exactly (vula/commerce/pop_links.py), numbered; pop_options keeps the
-- bill-id lists offered, in order, so "2" means what the person saw, and pop_doc_id is the filed proof,
-- so the answer links the document itself. Written only by the server. Idempotent.

alter table commerce_bank_transactions add column if not exists pop_options jsonb;
alter table commerce_bank_transactions add column if not exists pop_doc_id text;

comment on column commerce_bank_transactions.pop_options is
    'Proof of payment: the bill-id lists offered in its numbered WhatsApp question, in order.';
comment on column commerce_bank_transactions.pop_doc_id is
    'Proof of payment: the vula_filed_documents id of the filed proof, linked on the answer.';
