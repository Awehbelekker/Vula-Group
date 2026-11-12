-- 257_owner_briefs.sql — the owner's own WhatsApp briefs: off unless asked for, at the owner's time
-- (11–12 Nov 2026).
--
--   vula_tenant_config.owner_briefs
--       {"morning": {"on": true, "hour": 7, "minute": 0, "days": [0,1,2,3,4]},
--        "weekly":  {"on": true, "day": 0, "hour": 8, "minute": 0}}
--       morning = the morning brief (vula/owner_digest.py): what needs a person today (waiting
--                 customers, follow-ups, emails, draft quotes, documents, today's bookings) plus
--                 money paid, owed and due
--       weekly  = the weekly advisor (vula/owner_advisor.py)
--       Days 0 = Monday … 6 = Sunday, times SAST. Null, or a brief that isn't there, is OFF:
--       a brief goes out only when the owner switched it on — in onboarding, by asking on
--       WhatsApp, or in Team › Notices (vula/owner_briefs.py, vula/api/owner_briefs.py).
--
-- A column on an existing table (RLS already on); no new table. Idempotent.

alter table vula_tenant_config add column if not exists owner_briefs jsonb;
comment on column vula_tenant_config.owner_briefs is
    '{"morning": {"on","hour","minute","days"}, "weekly": {"on","day","hour","minute"}} — off unless the owner asked';
