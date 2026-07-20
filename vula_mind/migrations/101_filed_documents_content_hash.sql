-- 101_filed_documents_content_hash.sql (renumbered from 092 — collided with
-- 092_email_followup_summary.sql) — fixes a real bug found auditing digg-demo's HPC
-- documents: three distinct bank payment notifications (Solid Cape R50k, Edison R44k, Bauxite
-- R27,292.48), all named literally "Payment Notification.pdf" by the bank/WhatsApp forward,
-- silently failed to file at all. Migration 081's UNIQUE(tenant_id, source, filename) was built
-- to stop a genuine race-condition duplicate (two worker processes inserting the SAME file
-- twice) — but a generic, bank-reused filename is not a reliable proxy for "same file", so every
-- payment notification after the tenant's very first ever "Payment Notification.pdf" was
-- silently dropped by ON CONFLICT DO NOTHING, even though the WhatsApp confirmation message
-- claimed each one was filed.
--
-- Fix: add a content hash and widen the uniqueness key to include it, so the original
-- race-condition protection still holds (identical bytes = same file = still deduped) while two
-- DIFFERENT files that merely share a filename can both file correctly.

alter table vula_filed_documents add column if not exists content_hash text;

alter table vula_filed_documents
    drop constraint if exists vula_filed_documents_tenant_source_filename_key;

create unique index if not exists idx_filed_documents_tenant_source_filename_hash
    on vula_filed_documents (tenant_id, source, filename, content_hash);
