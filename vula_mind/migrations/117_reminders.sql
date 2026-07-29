-- ============================================================
-- Vula Group — Migration 117: reminders / commitments
-- No existing table was a clean fit for a general "remind me to X" reminder:
-- vula_field_tasks is construction-project-scoped, vula_email_followups is
-- email-uid-scoped, vula_project_tasks requires a non-null project. This is the
-- persistence layer for log_meeting's already-extracted action_items (previously only
-- embedded in the filed document's fields jsonb and then forgotten) and for a new
-- create_reminder/list_reminders/complete_reminder admin-agent tool set.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_reminders (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    created_by  text not null,                    -- WhatsApp phone of whoever set it
    text        text not null,
    due_at      timestamptz,                       -- nullable: an undated "someday" reminder is valid
    status      text not null default 'open',      -- open | done
    source      text default 'manual',             -- manual | log_meeting
    linked_contact_phone text,                      -- optional, links back to a commerce_contacts row
    nudged_at   timestamptz,                        -- set once the due-reminder nudge has fired
    created_at  timestamptz not null default now(),
    completed_at timestamptz
);
create index if not exists idx_vula_reminders_tenant on vula_reminders (tenant_id, status, due_at);

alter table vula_reminders enable row level security;
drop policy if exists "tenant_isolation" on vula_reminders;
create policy "tenant_isolation" on vula_reminders
    using (tenant_id = current_setting('app.tenant_id', true));
