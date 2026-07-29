-- ============================================================
-- Vula Group — Migration 115: RLS for shared/internal infra tables (default-deny)
-- Part of the security remediation pass (VULA_SECURITY_AND_GAPS_BRIEF.md). These 3
-- tables have no tenant concept at all — they're global, cross-tenant infrastructure
-- the app only ever touches via the service-role key (which bypasses RLS entirely).
-- Enable RLS with NO permissive policy: closes the "RLS off" gap for completeness
-- without inventing a tenant boundary that doesn't apply to this data.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_scheduler_lock enable row level security;
-- Intentionally no policy: single shared cross-worker lock row, service-role only.

alter table vula_wa_msg_dedup enable row level security;
-- Intentionally no policy: global Meta message-id dedup ledger, service-role only.

alter table commerce_geo_cache enable row level security;
-- Intentionally no policy: shared geocode cache keyed by normalized query string,
-- service-role only, contains no tenant-identifying or customer data.
