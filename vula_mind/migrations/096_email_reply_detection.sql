-- ============================================================
-- Vula Group — Migration 096: auto-detect when a follow-up has been replied to
-- Sent-folder syncing (094) means outbound mail is now actually read — this uses that to
-- close the loop: when a Sent email is a reply to a message tracked in
-- vula_email_followups, mark that follow-up done automatically instead of leaving it open
-- until someone manually clears it.
--
-- message_id stores the ORIGINAL inbound email's Message-ID header at follow-up creation
-- time, so a later Sent email's In-Reply-To/References headers (which name the message
-- they're replying to) can be matched against it directly — the reliable way to detect a
-- reply, vs. guessing from subject line alone.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_email_followups
    add column if not exists message_id text;

create index if not exists idx_email_followups_message_id
    on vula_email_followups (tenant_id, message_id) where message_id is not null;
