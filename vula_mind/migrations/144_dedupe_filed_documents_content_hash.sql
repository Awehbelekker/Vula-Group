-- 144_dedupe_filed_documents_content_hash.sql — completes migration 143, which could not have
-- actually finished: 10 real pre-existing duplicate (tenant_id, source, content_hash) groups
-- already existed on the table (the exact WhatsApp-redelivery duplicates 143 was written to
-- prevent going forward), so its `create unique index ... (tenant_id, source, content_hash)`
-- must have failed against that duplicate data — while its `drop constraint`/`drop index`
-- statements for the OLD (tenant_id, source, filename) key still went through.
--
-- Net effect, confirmed live 2026-08-27 (a real gerflor billboard-photo upload): the table was
-- left with NEITHER the old nor the new unique constraint in place. Every document filing since
-- has failed with Postgres error 42P10 ("no unique or exclusion constraint matching the ON
-- CONFLICT specification") on BOTH doc_filing.py::file_document()'s primary insert attempt
-- (on_conflict="tenant_id,source,content_hash") AND its fallback retry
-- (on_conflict="tenant_id,source,filename") — the extraction itself still worked (it's a
-- separate legacy table write), but the document was never durably filed, and the WhatsApp
-- reply still said "✅ Filed" regardless (a separate, smaller honesty fix — see whatsapp.py).
--
-- Fix: de-duplicate the existing rows (keep the earliest per group — matches what the OLD
-- filename-based key would already have kept, since these are genuine same-content
-- redeliveries), THEN create the index migration 143 always intended, which can now actually
-- succeed. Re-running 143's own drop statements too (idempotent, in case this runs standalone).

delete from vula_filed_documents a
using vula_filed_documents b
where a.tenant_id = b.tenant_id
  and a.source = b.source
  and a.content_hash = b.content_hash
  and a.content_hash is not null
  and a.id <> b.id
  and (a.created_at, a.id) > (b.created_at, b.id);

alter table vula_filed_documents
    drop constraint if exists vula_filed_documents_tenant_source_filename_key;

drop index if exists idx_filed_documents_tenant_source_filename_hash;

create unique index if not exists idx_filed_documents_tenant_source_hash
    on vula_filed_documents (tenant_id, source, content_hash)
    where content_hash is not null;
