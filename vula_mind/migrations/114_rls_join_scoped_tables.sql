-- ============================================================
-- Vula Group — Migration 114: RLS for tables with no tenant_id column
-- Part of the security remediation pass (VULA_SECURITY_AND_GAPS_BRIEF.md). These 7
-- tables have no tenant_id column of their own — they're scoped via a foreign key to
-- a parent table that IS tenant-scoped (and already gets RLS from 002/013/012/041 or
-- migration 113 in this same pass). Column types verified directly against each
-- CREATE TABLE before writing these joins (uuid<->uuid or text<->text throughout —
-- no casts needed).
-- Run in Supabase SQL editor, AFTER 113 (parent tables must have RLS+policy first).
-- ============================================================

alter table commerce_automation_log enable row level security;
drop policy if exists "tenant_isolation" on commerce_automation_log;
create policy "tenant_isolation" on commerce_automation_log
    using (exists (
        select 1 from commerce_automations a
        where a.id = commerce_automation_log.automation_id
          and a.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table commerce_cart_items enable row level security;
drop policy if exists "tenant_isolation" on commerce_cart_items;
create policy "tenant_isolation" on commerce_cart_items
    using (exists (
        select 1 from commerce_carts c
        where c.id = commerce_cart_items.cart_id
          and c.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table commerce_order_items enable row level security;
drop policy if exists "tenant_isolation" on commerce_order_items;
create policy "tenant_isolation" on commerce_order_items
    using (exists (
        select 1 from commerce_orders o
        where o.id = commerce_order_items.order_id
          and o.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table vula_approval_steps enable row level security;
drop policy if exists "tenant_isolation" on vula_approval_steps;
create policy "tenant_isolation" on vula_approval_steps
    using (exists (
        select 1 from vula_approvals ap
        where ap.id = vula_approval_steps.approval_id
          and ap.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table vula_field_evidence enable row level security;
drop policy if exists "tenant_isolation" on vula_field_evidence;
create policy "tenant_isolation" on vula_field_evidence
    using (exists (
        select 1 from vula_field_tasks t
        where t.id = vula_field_evidence.task_id
          and t.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table vula_field_sign_offs enable row level security;
drop policy if exists "tenant_isolation" on vula_field_sign_offs;
create policy "tenant_isolation" on vula_field_sign_offs
    using (exists (
        select 1 from vula_field_tasks t
        where t.id = vula_field_sign_offs.task_id
          and t.tenant_id = current_setting('app.tenant_id', true)
    ));

alter table vula_page_versions enable row level security;
drop policy if exists "tenant_isolation" on vula_page_versions;
create policy "tenant_isolation" on vula_page_versions
    using (exists (
        select 1 from vula_pages p
        where p.id = vula_page_versions.page_id
          and p.tenant_id = current_setting('app.tenant_id', true)
    ));
