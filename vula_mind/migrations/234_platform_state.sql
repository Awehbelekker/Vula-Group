-- 234_platform_state.sql — small platform-wide values that must survive a redeploy.
--
-- 31 Oct 2026: the readiness alert ("🔧 Vula setup: N thing(s) still to do") is meant to go to
-- the operator only when the list changes. It remembered the last list in a file on the server's
-- disk, and the Railway service has no volume, so every deploy wiped it and the same list went
-- out again (vula/readiness.py::is_new). Not tenant data: one row per key, e.g.
--
--   key 'readiness_last'   value: sha256 of the last readiness summary sent
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_platform_state (
    key         text primary key,
    value       text,
    updated_at  timestamptz not null default now()
);
alter table vula_platform_state enable row level security;
