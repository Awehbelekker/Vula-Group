-- 257_email_all_folders.sql — every connected mailbox is read in all its folders, not only Inbox and Sent.
--
-- 11 Nov 2026 (Ian, DIGG): HPC's payment certificates 1 and 5-13 never reached Vula, though the
-- thread was in Judy's mailbox. The sync only ever read INBOX and the Sent folder, so anything a
-- mail rule (or the owner) moved into a folder was never seen. "This needs to be for all tenants."
--
--   sync_all_folders  read every other selectable folder too (not Trash, Junk, Drafts, Gmail's
--                     All Mail / Starred / Important). On for every mailbox, existing and new;
--                     set false to keep one mailbox to Inbox and Sent.
--   folder_cursors    per folder: {"<folder>": {"uidvalidity": n, "last_uid": n}} — IMAP UIDs
--                     only mean something within one folder and one UIDVALIDITY.
-- Old mail found in a folder for the first time is filed without messaging anyone (it waits under
-- To check); see vula/email_imap/sync.py::HISTORIC_DAYS.
-- Idempotent.

alter table vula_email_accounts add column if not exists sync_all_folders boolean not null default true;
alter table vula_email_accounts add column if not exists folder_cursors jsonb not null default '{}'::jsonb;
alter table vula_email_accounts enable row level security;
