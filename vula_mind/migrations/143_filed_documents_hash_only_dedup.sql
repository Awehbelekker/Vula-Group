-- 143_filed_documents_hash_only_dedup.sql — drop filename from the dedup key entirely.
--
-- Real bug found live 2026-08-27, auditing a digg-demo "loop" report: a genuine WhatsApp
-- redelivery of the SAME Proof of Payment (confirmed identical content_hash) filed TWICE,
-- because Vula's own auto-generated filename bakes in a MINUTE-precision timestamp
-- (_friendly_document_name) — "Proof of Payment 20260827-1115.pdf" vs "...1116.pdf" — so even
-- byte-identical redeliveries get a different filename on each processing attempt, defeating
-- migration 101's (tenant_id, source, filename, content_hash) uniqueness key.
--
-- Migration 101 widened the key to ADD content_hash because a generic bank-provided filename
-- ("Payment Notification.pdf") was wrongly deduping genuinely DIFFERENT documents. That fix was
-- correct — but it should have gone all the way to making content_hash the SOLE key rather than
-- keeping filename in it too: filename is a label (sometimes Vula's own generated one, sometimes
-- the bank's own generic one), never a reliable proxy for "is this the same document." Content
-- bytes are. Dropping filename from the key fixes both directions at once: different bytes
-- sharing a filename still file separately (content_hash differs), and identical bytes under a
-- different filename now correctly dedupe (content_hash matches) — closing the exact case that
-- slipped through today.

alter table vula_filed_documents
    drop constraint if exists vula_filed_documents_tenant_source_filename_key;

drop index if exists idx_filed_documents_tenant_source_filename_hash;

-- Partial (content_hash is not null) so legacy pre-101 rows with no hash never collide with
-- each other under this index — matches Postgres's own NULL-distinct behaviour explicitly.
create unique index if not exists idx_filed_documents_tenant_source_hash
    on vula_filed_documents (tenant_id, source, content_hash)
    where content_hash is not null;
