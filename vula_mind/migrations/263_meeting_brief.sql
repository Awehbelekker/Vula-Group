-- 263_meeting_brief.sql — put a brief on a meeting's reminder, only when the person asks (12 Nov 2026).
--
-- "Remind me an hour before with a brief" → the reminder also carries a few lines on who they're
-- meeting: last WhatsApp messages, emails waiting, open quotes and unpaid invoices (if they may see
-- money), last orders, last meeting notes (vula/meeting_brief.py). Idempotent.

alter table vula_meetings add column if not exists with_brief boolean not null default false;
