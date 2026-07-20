-- ============================================================
-- Vula Group — Migration 095: email sync failure tracking
-- A mailbox whose IMAP login starts failing (revoked app-password, host change) synced
-- silently forever before this — status stayed "connected", nobody was told, and it was
-- indistinguishable from "no new mail." sync_fail_count lets the sync loop notify the team
-- once it's crossed a few consecutive failures (not on the first blip) and flip status to
-- "error", then clear itself automatically the moment a sync succeeds again.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_email_accounts
    add column if not exists sync_fail_count integer not null default 0;
