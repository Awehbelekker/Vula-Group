-- 145_filed_documents_hash_index_non_partial.sql — fixes migration 144's index definition.
--
-- Confirmed live 2026-08-27: after 144 ran (duplicates cleared, index created, PostgREST schema
-- cache reloaded), document filing STILL failed with the same Postgres error 42P10. Root cause
-- is different from what 143/144 assumed: the index was created PARTIAL
-- (`where content_hash is not null`), and Postgres will only infer a partial unique index as an
-- ON CONFLICT arbiter when the conflict clause ALSO repeats the predicate — which Supabase's
-- REST API (doc_filing.py's `upsert(..., on_conflict="tenant_id,source,content_hash")`) has no
-- way to pass through. A column-list-only on_conflict can never match a partial index, no
-- matter how fresh the schema cache is.
--
-- The partial predicate was unnecessary in the first place: Postgres unique indexes already
-- treat NULL as never equal to NULL by default (standard SQL semantics), so legacy rows with
-- content_hash=NULL were already guaranteed not to collide with each other under a plain,
-- non-partial unique index — the `where content_hash is not null` clause added nothing but
-- broke ON CONFLICT inference. Drop and recreate as a plain index.

drop index if exists idx_filed_documents_tenant_source_hash;

create unique index if not exists idx_filed_documents_tenant_source_hash
    on vula_filed_documents (tenant_id, source, content_hash);
