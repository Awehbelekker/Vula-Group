-- ============================================================
-- Vula Group — Migration 081: dedupe + uniquely constrain vula_filed_documents
--
-- Real bug found investigating digg-demo's data: the app runs with WEB_CONCURRENCY=2 (two
-- separate uvicorn worker processes). file_document()'s de-dup check was SELECT-then-INSERT
-- with no DB-level backstop and an in-memory lock that only protects within one process — two
-- workers racing the same email-sync poll (or the same WhatsApp attachment) could both pass the
-- "does this already exist?" check before either had inserted, producing two rows for the same
-- (tenant_id, source, filename). Confirmed live: several exact duplicates for digg-demo, both
-- from email sync (invoice PDFs) and from WhatsApp uploads (wamid-named images/receipts).
--
-- This migration first removes existing duplicates (keeping the earliest row per group), then
-- adds a real UNIQUE constraint so file_document()'s insert (changed to an upsert in the same
-- commit) can never create a second row for the same file again, regardless of worker count.
-- Run in Supabase SQL editor.
-- ============================================================

delete from vula_filed_documents a
using vula_filed_documents b
where a.tenant_id = b.tenant_id
  and a.source = b.source
  and a.filename = b.filename
  and a.created_at > b.created_at;

alter table vula_filed_documents
    add constraint vula_filed_documents_tenant_source_filename_key
    unique (tenant_id, source, filename);
