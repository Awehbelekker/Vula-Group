-- ============================================================
-- Vula Group — Migration 113: RLS for direct tenant_id-scoped tables
-- Part of the security remediation pass (VULA_SECURITY_AND_GAPS_BRIEF.md). These 36
-- tables all have a direct tenant_id column and were confirmed (by parsing every
-- CREATE TABLE vs ENABLE ROW LEVEL SECURITY statement across all migrations) to have
-- no RLS enabled anywhere. Same tenant_isolation pattern as 004/039/112 — no
-- schema changes, no code changes, additive-only (service-role access is unaffected;
-- RLS only restricts non-service-role callers).
-- Run in Supabase SQL editor.
-- ============================================================

alter table commerce_automations enable row level security;
drop policy if exists "tenant_isolation" on commerce_automations;
create policy "tenant_isolation" on commerce_automations
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_broadcast_recipients enable row level security;
drop policy if exists "tenant_isolation" on commerce_broadcast_recipients;
create policy "tenant_isolation" on commerce_broadcast_recipients
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_campaigns enable row level security;
drop policy if exists "tenant_isolation" on commerce_campaigns;
create policy "tenant_isolation" on commerce_campaigns
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_consent enable row level security;
drop policy if exists "tenant_isolation" on commerce_consent;
create policy "tenant_isolation" on commerce_consent
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_purchase_orders enable row level security;
drop policy if exists "tenant_isolation" on commerce_purchase_orders;
create policy "tenant_isolation" on commerce_purchase_orders
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_saved_copy enable row level security;
drop policy if exists "tenant_isolation" on commerce_saved_copy;
create policy "tenant_isolation" on commerce_saved_copy
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_segments enable row level security;
drop policy if exists "tenant_isolation" on commerce_segments;
create policy "tenant_isolation" on commerce_segments
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_ai_usage enable row level security;
drop policy if exists "tenant_isolation" on vula_ai_usage;
create policy "tenant_isolation" on vula_ai_usage
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_approvals enable row level security;
drop policy if exists "tenant_isolation" on vula_approvals;
create policy "tenant_isolation" on vula_approvals
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_code_library enable row level security;
drop policy if exists "tenant_isolation" on vula_code_library;
create policy "tenant_isolation" on vula_code_library
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_document_extractions enable row level security;
drop policy if exists "tenant_isolation" on vula_document_extractions;
create policy "tenant_isolation" on vula_document_extractions
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_email_contacts enable row level security;
drop policy if exists "tenant_isolation" on vula_email_contacts;
create policy "tenant_isolation" on vula_email_contacts
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_email_followups enable row level security;
drop policy if exists "tenant_isolation" on vula_email_followups;
create policy "tenant_isolation" on vula_email_followups
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_field_assignments enable row level security;
drop policy if exists "tenant_isolation" on vula_field_assignments;
create policy "tenant_isolation" on vula_field_assignments
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_field_clickup_map enable row level security;
drop policy if exists "tenant_isolation" on vula_field_clickup_map;
create policy "tenant_isolation" on vula_field_clickup_map
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_field_contractors enable row level security;
drop policy if exists "tenant_isolation" on vula_field_contractors;
create policy "tenant_isolation" on vula_field_contractors
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_field_tasks enable row level security;
drop policy if exists "tenant_isolation" on vula_field_tasks;
create policy "tenant_isolation" on vula_field_tasks
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_field_walkthroughs enable row level security;
drop policy if exists "tenant_isolation" on vula_field_walkthroughs;
create policy "tenant_isolation" on vula_field_walkthroughs
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_filed_documents enable row level security;
drop policy if exists "tenant_isolation" on vula_filed_documents;
create policy "tenant_isolation" on vula_filed_documents
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_filing_rules enable row level security;
drop policy if exists "tenant_isolation" on vula_filing_rules;
create policy "tenant_isolation" on vula_filing_rules
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_infra_snapshot enable row level security;
drop policy if exists "tenant_isolation" on vula_infra_snapshot;
create policy "tenant_isolation" on vula_infra_snapshot
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_boq enable row level security;
drop policy if exists "tenant_isolation" on vula_project_boq;
create policy "tenant_isolation" on vula_project_boq
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_briefs enable row level security;
drop policy if exists "tenant_isolation" on vula_project_briefs;
create policy "tenant_isolation" on vula_project_briefs
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_budgets enable row level security;
drop policy if exists "tenant_isolation" on vula_project_budgets;
create policy "tenant_isolation" on vula_project_budgets
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_codes enable row level security;
drop policy if exists "tenant_isolation" on vula_project_codes;
create policy "tenant_isolation" on vula_project_codes
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_finances enable row level security;
drop policy if exists "tenant_isolation" on vula_project_finances;
create policy "tenant_isolation" on vula_project_finances
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_messages enable row level security;
drop policy if exists "tenant_isolation" on vula_project_messages;
create policy "tenant_isolation" on vula_project_messages
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_tasks enable row level security;
drop policy if exists "tenant_isolation" on vula_project_tasks;
create policy "tenant_isolation" on vula_project_tasks
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_team enable row level security;
drop policy if exists "tenant_isolation" on vula_project_team;
create policy "tenant_isolation" on vula_project_team
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_project_threads enable row level security;
drop policy if exists "tenant_isolation" on vula_project_threads;
create policy "tenant_isolation" on vula_project_threads
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_projects enable row level security;
drop policy if exists "tenant_isolation" on vula_projects;
create policy "tenant_isolation" on vula_projects
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_qs_rates enable row level security;
drop policy if exists "tenant_isolation" on vula_qs_rates;
create policy "tenant_isolation" on vula_qs_rates
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_team_members enable row level security;
drop policy if exists "tenant_isolation" on vula_team_members;
create policy "tenant_isolation" on vula_team_members
    using (tenant_id = current_setting('app.tenant_id', true));

-- tenant_id is nullable here — a null row simply won't match any tenant's policy and
-- stays visible to the service-role connection only, which is correct (it's an
-- internal failure log, not tenant-facing data).
alter table vula_webhook_failures enable row level security;
drop policy if exists "tenant_isolation" on vula_webhook_failures;
create policy "tenant_isolation" on vula_webhook_failures
    using (tenant_id = current_setting('app.tenant_id', true));

alter table whatsapp_conversations enable row level security;
drop policy if exists "tenant_isolation" on whatsapp_conversations;
create policy "tenant_isolation" on whatsapp_conversations
    using (tenant_id = current_setting('app.tenant_id', true));

alter table whatsapp_draft_orders enable row level security;
drop policy if exists "tenant_isolation" on whatsapp_draft_orders;
create policy "tenant_isolation" on whatsapp_draft_orders
    using (tenant_id = current_setting('app.tenant_id', true));
