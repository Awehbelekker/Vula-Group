-- 173_security_advisor_hardening.sql — clear the two fixable findings from Supabase's security
-- advisor (mcp__Supabase__get_advisors) on vula-production, found doing a go-live infra check.
--
-- 1. rls_enabled_no_policy (7 tables): each of these has RLS enabled but no policy at all, which
--    means PostgREST denies every request to anon/authenticated — not a leak, but not
--    intentional either, and worth being explicit about. Confirmed by grepping vula_dashboard's
--    src for each table name: none are queried from a client-side Supabase call, only from
--    vula_mind's service-role client (which bypasses RLS regardless of policy). Same
--    service_role-only pattern already used for vula_wa_outbound (153), vula_business_rules
--    (152) and vula_voice_retry_queue (148) — this just extends it to the 7 tables the advisor
--    flagged as missing it.
--
-- 2. function_search_path_mutable (9 functions): none of these are SECURITY DEFINER, so this
--    isn't a privilege-escalation path, but an unset search_path is still the standard advisory
--    finding Supabase's linter flags — pin it explicitly rather than leave it to whatever role
--    happens to call the function.
--
-- Not addressed here (both dashboard-only, no migration/API path exists for either):
--   - Storage → Settings → Global file size limit (still gating the qdrant-backups bucket's
--     500MB per-bucket limit from migration 172 — see docs/dr.md)
--   - Auth → Providers → Email → "leaked password protection" toggle

-- ── 1. Explicit service_role-only policies ──────────────────────────────────────

drop policy if exists commerce_geo_cache_service on commerce_geo_cache;
create policy commerce_geo_cache_service on commerce_geo_cache
    for all to service_role using (true) with check (true);

drop policy if exists commerce_pending_confirmations_service on commerce_pending_confirmations;
create policy commerce_pending_confirmations_service on commerce_pending_confirmations
    for all to service_role using (true) with check (true);

drop policy if exists vula_admin_audit_service on vula_admin_audit;
create policy vula_admin_audit_service on vula_admin_audit
    for all to service_role using (true) with check (true);

drop policy if exists vula_merchant_audit_service on vula_merchant_audit;
create policy vula_merchant_audit_service on vula_merchant_audit
    for all to service_role using (true) with check (true);

drop policy if exists vula_platform_feedback_service on vula_platform_feedback;
create policy vula_platform_feedback_service on vula_platform_feedback
    for all to service_role using (true) with check (true);

drop policy if exists vula_reasoning_telemetry_service on vula_reasoning_telemetry;
create policy vula_reasoning_telemetry_service on vula_reasoning_telemetry
    for all to service_role using (true) with check (true);

drop policy if exists vula_scheduler_lock_service on vula_scheduler_lock;
create policy vula_scheduler_lock_service on vula_scheduler_lock
    for all to service_role using (true) with check (true);

-- ── 2. Pin search_path on flagged functions ─────────────────────────────────────

alter function public.update_updated_at() set search_path = public;
alter function public.set_updated_at() set search_path = public;
alter function public.decrement_product_stock(p_tenant_id text, p_product_id uuid, p_delta integer)
    set search_path = public;
alter function public.decrement_variant_stock(p_variant_id uuid, p_delta integer)
    set search_path = public;
alter function public.increment_discount_code_usage(p_code_id uuid)
    set search_path = public;
alter function public.post_journal_entry(p_tenant_id text, p_entry_date date, p_description text,
    p_source_type text, p_source_id text, p_lines jsonb)
    set search_path = public;
alter function public.next_document_number(p_tenant_id text, p_counter_key text)
    set search_path = public;
alter function public.reserve_product_stock(p_tenant_id text, p_product_id uuid, p_qty integer)
    set search_path = public;
alter function public.reserve_variant_stock(p_variant_id uuid, p_qty integer)
    set search_path = public;
