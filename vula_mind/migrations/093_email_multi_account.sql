-- ============================================================
-- Vula Group — Migration 093: multiple connected email accounts per tenant
-- Was: one mailbox per tenant (vula_email_accounts.tenant_id was itself UNIQUE, so
-- connecting a second mailbox silently overwrote the first). Some clients run separate
-- admin@/sales@/personal mailboxes that all need to feed the same tenant's KB, contact
-- directory, and document filing — this migration lets a tenant connect several.
--
-- Design:
-- - Replace the tenant_id-only UNIQUE with UNIQUE(tenant_id, email) — many rows per
--   tenant now allowed, but the same address can't be connected twice.
-- - is_primary marks the one account that single-mailbox-only features (the WhatsApp/
--   portal AI email skill's default mailbox, bank-statement-password handling, the
--   notify_phone fallback) fall back to when nothing more specific is asked for. Exactly
--   one TRUE per tenant, enforced by a partial unique index rather than app-level trust.
--   Every existing row becomes primary (each tenant currently has exactly one account).
-- - vula_email_followups.email_uid is an IMAP UID, which is only unique WITHIN a mailbox —
--   with two accounts both syncing, uid=42 from each would collide on the old
--   UNIQUE(tenant_id, email_uid) and silently overwrite each other. Add account_id and
--   widen the uniqueness to (tenant_id, account_id, email_uid). Existing rows backfill to
--   the tenant's (now-primary) account, which is correct since it's the only account that
--   existed when they were written.
-- - vula_filed_documents gets an account_id for provenance ("which mailbox did this attachment
--   come from") — nullable, NOT part of the dedup key (content_hash already handles dedup
--   correctly across mailboxes: two accounts receiving the same attachment should still
--   dedupe to one filed copy).
-- Run in Supabase SQL editor.
-- ============================================================

-- ── vula_email_accounts: allow several rows per tenant ──────────────────────────
alter table vula_email_accounts drop constraint if exists vula_email_accounts_tenant_id_key;

alter table vula_email_accounts
    add column if not exists is_primary boolean not null default false;

-- Every row that exists today is the only account its tenant has — make it primary.
update vula_email_accounts set is_primary = true where is_primary = false;

alter table vula_email_accounts
    add constraint vula_email_accounts_tenant_email_key unique (tenant_id, email);

-- Exactly one primary per tenant — a partial unique index is the standard Postgres way
-- to enforce "at most one TRUE per group" (a plain UNIQUE(tenant_id) would forbid the
-- other, non-primary accounts from existing at all).
create unique index if not exists idx_email_accounts_one_primary
    on vula_email_accounts (tenant_id) where is_primary;

create index if not exists idx_vula_email_accounts_tenant_status
    on vula_email_accounts (tenant_id, status);

-- ── vula_email_followups: IMAP UIDs are only unique within a mailbox ────────────
alter table vula_email_followups drop constraint if exists vula_email_followups_tenant_id_email_uid_key;

alter table vula_email_followups
    add column if not exists account_id uuid references vula_email_accounts(id);

-- Backfill existing rows to their tenant's (sole, now-primary) account.
update vula_email_followups f
set account_id = a.id
from vula_email_accounts a
where f.account_id is null and a.tenant_id = f.tenant_id and a.is_primary;

alter table vula_email_followups
    add constraint vula_email_followups_tenant_account_uid_key unique (tenant_id, account_id, email_uid);

-- ── vula_filed_documents: which mailbox an emailed attachment came from ─────────
alter table vula_filed_documents
    add column if not exists account_id uuid references vula_email_accounts(id);
