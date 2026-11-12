-- 257_owner_briefs.sql — each business's own on/off for the owner briefs on WhatsApp (11 Nov 2026).
--
--   vula_tenant_config.owner_briefs
--       {"morning": bool, "weekly": bool}
--       morning = the weekday-morning brief (vula/owner_digest.py): money plus what needs a
--                 person today (waiting customers, follow-ups, emails, draft quotes, documents,
--                 today's bookings)
--       weekly  = the Monday advisor (vula/owner_advisor.py)
--       A missing key (or null) follows the platform default (OWNER_DIGEST_ENABLED /
--       OWNER_ADVISOR_ENABLED). Set from Team › Briefs (vula/api/owner_briefs.py).
--
-- A column on an existing table (RLS already on); no new table. Idempotent.

alter table vula_tenant_config add column if not exists owner_briefs jsonb;
comment on column vula_tenant_config.owner_briefs is
    '{"morning": bool, "weekly": bool} — a missing key follows the platform default';
