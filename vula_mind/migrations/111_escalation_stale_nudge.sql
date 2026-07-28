-- 111_escalation_stale_nudge.sql — tracks whether a stale open escalation has already been
-- nudged, so the periodic staleness check (vula/escalation.py find_stale_open_escalations,
-- vula/api/server.py's tenant-agnostic scheduler loop) doesn't re-notify the same escalation
-- every poll cycle. Part of generalizing proactive re-engagement past commerce-only tenants
-- (2026-07-28): a customer's open question that's gone unanswered too long used to just
-- silently expire at 48h (open_escalation_for_helper) with nobody told a question was dropped.

alter table vula_escalations add column if not exists stale_notified_at timestamptz;
