-- 240_schema_migrations.sql — a record of which migrations/*.sql files have run, for tools/migrate.py.
--
-- 31 Oct 2026 (go-live audit, Phase 1 "Migration runner"). Until now migrations were pasted by
-- hand into the Supabase SQL editor and the only way to tell one was missed was the sentinel
-- probes (vula/startup_checks.py::_SENTINELS, Master › Health). tools/migrate.py now applies
-- pending files over DATABASE_URL and writes one row here per file, after that file succeeds:
--
--   filename    the file's name, e.g. '240_schema_migrations.sql' (primary key)
--   number      its leading number (240)
--   sha256      hash of the file as it ran (CRLF normalised to LF), so an edit after the fact
--               shows up as "changed" in `migrate.py status`
--   applied_by  who ran it (OS user / MIGRATE_APPLIED_BY)
--   baseline    true when the row was written by `migrate.py baseline` — the file was already
--               applied by hand before the runner existed, and the runner did not run it
--
-- Written only by tools/migrate.py over a direct Postgres connection, read by nothing in the
-- dashboard. RLS on, no policies: PostgREST denies anon/authenticated, the service role and the
-- postgres owner bypass RLS. Idempotent (tools/migrate.py also runs this file to bootstrap).

create table if not exists vula_schema_migrations (
    filename    text primary key,
    number      int not null,
    sha256      text not null,
    applied_at  timestamptz default now(),
    applied_by  text,
    baseline    boolean default false
);
alter table vula_schema_migrations enable row level security;
