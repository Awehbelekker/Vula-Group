-- 264_outlook_calendar.sql — meetings from the connected Outlook calendar (12 Nov 2026).
--
-- vula/outlook_calendar.py reads the next 7 days of the business's connected Microsoft account
-- (Calendars.Read) into vula_meetings, so they show in "what meetings do I have?", the Today
-- list, the morning brief and team activity. source/external_id keep them apart from meetings
-- booked on WhatsApp and stop duplicates. A person is reminded of their calendar meetings only if
-- they asked ("remind me 30 minutes before my Outlook meetings") — calendar_remind_minutes, and
-- calendar_brief for a brief with it. Idempotent.

alter table vula_meetings add column if not exists source text not null default 'whatsapp';
alter table vula_meetings add column if not exists external_id text;
create unique index if not exists vula_meetings_external
    on vula_meetings (tenant_id, external_id) where external_id is not null;

alter table vula_team_members add column if not exists calendar_remind_minutes integer
    check (calendar_remind_minutes is null or calendar_remind_minutes between 5 and 2880);
alter table vula_team_members add column if not exists calendar_brief boolean not null default false;
