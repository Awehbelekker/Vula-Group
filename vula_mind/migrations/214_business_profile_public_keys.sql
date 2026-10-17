-- 214: which business-profile answers customers may see (2026-10-17, KB retrieval 1b).
-- The profile (migration 189) became ONE knowledge-base document that every skill retrieved —
-- the customer assistant included — although it mixes customer facts (hours, delivery areas,
-- payment methods, returns) with internal ones (who does what in the team, tax regime, payroll).
-- It is now written as two documents: one customers can see, one for the team. public_keys is
-- the owner's list of answers on the customer side; null = the defaults in
-- vula/commerce/business_profile.py. RLS already on (189). Idempotent; nothing dropped.

alter table vula_business_profile add column if not exists public_keys jsonb;
