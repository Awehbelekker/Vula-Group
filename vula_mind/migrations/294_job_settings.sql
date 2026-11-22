-- 294_job_settings.sql — per-business settings on a scheduled job.
--
-- 22 Nov 2026 (conversation intelligence, step 1: docs/plans/conversation-intelligence.md). The
-- handoff watch reminded the person handling a paused chat after 30 minutes and gave the chat back
-- to Vula after 2 hours, the same for every business. A job's own numbers (for the handoff watch:
-- remind_after_minutes, resume_after_minutes) now live here, set in the Scheduling tab. Missing
-- or empty = the job's defaults (vula/commerce/job_config.py). Idempotent.

alter table commerce_scheduled_job_config
    add column if not exists settings jsonb not null default '{}'::jsonb;

alter table commerce_scheduled_job_config enable row level security;
