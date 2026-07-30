-- ============================================================
-- Vula Group — Migration 120: captured Sent-folder email excerpts for voice-profile learning
-- vula/email_imap/sync.py already fetches the owner's Sent-folder body text (to build contacts
-- and resolve follow-ups) then discards it. This adds a small table to keep a short, redacted
-- excerpt of genuinely human-composed sent emails (AI-composed/auto-sent mail is tagged with an
-- X-Vula-Sent header at send time and excluded — see _build() in vula/email_imap/service.py and
-- the filter in vula/email_imap/sync.py's _do_email_sync) so voice_profile.py can learn from
-- real sent emails, not just WhatsApp replies.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_email_voice_samples (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    excerpt     text not null,
    created_at  timestamptz not null default now()
);
create index if not exists idx_email_voice_samples_tenant on vula_email_voice_samples (tenant_id, created_at desc);

alter table vula_email_voice_samples enable row level security;
drop policy if exists "tenant_isolation" on vula_email_voice_samples;
create policy "tenant_isolation" on vula_email_voice_samples
    using (tenant_id = current_setting('app.tenant_id', true));
