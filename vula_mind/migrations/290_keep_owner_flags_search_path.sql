-- 290_keep_owner_flags_search_path.sql — pin the search path of 285's trigger function.
--
-- 18 Nov 2026: Supabase's security advisor flagged vula_filed_documents_keep_owner_flags (migration
-- 285) as "function search path mutable", as 241 fixed for the earlier trigger functions. The
-- function only reads its own row, so pinning it changes nothing it does. Idempotent.

alter function vula_filed_documents_keep_owner_flags() set search_path = public, pg_temp;
