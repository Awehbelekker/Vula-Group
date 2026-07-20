-- 092_email_followup_summary.sql — AI skim-summary + urgency/tone for follow-up emails.
--
-- The dashboard's Follow-ups list only showed a raw 110-char body slice, so users had to
-- open every email to tell what it actually needed. Adds a one-sentence AI summary plus a
-- coarse urgency/tone read (computed once, at sync time, only for emails that already
-- passed the _needs_reply filter — a small subset, not every synced email).
alter table vula_email_followups add column if not exists summary text;
alter table vula_email_followups add column if not exists urgency text default 'normal';   -- high | normal | low
alter table vula_email_followups add column if not exists tone text;
