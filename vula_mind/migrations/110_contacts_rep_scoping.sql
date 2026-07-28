-- ============================================================
-- Vula Group — Migration 110: rep-scoped contacts + company/title fields
-- Lets a team member (e.g. a sales rep sharing the tenant's WhatsApp number with the
-- owner/other reps) capture their own contact book from business cards without every
-- other team member's contacts drowning it out, and adds the company/title fields a
-- scanned business card actually carries (commerce_contacts had neither before).
-- Run in Supabase SQL editor.
-- ============================================================

alter table commerce_contacts add column if not exists company text;
alter table commerce_contacts add column if not exists title text;
alter table commerce_contacts add column if not exists created_by text;  -- team member's WhatsApp phone, null = shop-wide (import/customer-order capture)

create index if not exists idx_commerce_contacts_created_by on commerce_contacts (tenant_id, created_by);
