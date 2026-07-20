-- ============================================================
-- Vula Group — Migration 094: sync the Sent folder too, not just Inbox
-- Every IMAP sync call so far has hard-coded m.select("INBOX") — outbound mail (quotes,
-- invoices, correspondence the tenant SENT) was invisible to the KB/contact directory/
-- document filing. IMAP UIDs are only unique WITHIN a folder, so Sent needs its OWN sync
-- cursor — reusing last_sync_uid would either skip Sent mail or wrongly treat Inbox/Sent
-- UIDs as comparable positions in the same sequence.
--
-- sync_sent defaults to true (the whole point is capturing outbound documents), but stays
-- a per-account toggle in case a mailbox's Sent folder is huge/irrelevant.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_email_accounts
    add column if not exists last_sync_uid_sent integer not null default 0,
    add column if not exists sync_sent boolean not null default true;
