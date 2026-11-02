-- 241_trigger_search_path.sql — pin search_path on the three Tap to Pay trigger functions.
--
-- 31 Oct 2026 (go-live audit, Phase 2). Supabase's security advisor flags these as
-- function_search_path_mutable: they were created without SET search_path in
-- 199_tap_to_pay.sql (kb_ledger_append_only), 205_tap_tax_invoices.sql (kb_tax_invoice_immutable)
-- and 232_tap_audit.sql (kb_audit_append_only). None is SECURITY DEFINER, so this is not a
-- privilege-escalation path; it is the same advisory fix migration 173 applied to nine other
-- functions.
--
-- Metadata only: ALTER FUNCTION ... SET search_path changes the function's config, not its body,
-- and the triggers that call them are untouched. The ledger stays append-only, tax invoices stay
-- immutable and the audit log stays append-only, exactly as before.
--
-- Each ALTER runs only if the function exists (a database that never had Tap to Pay applied is
-- left alone). Idempotent: re-running sets the same value.

do $$
declare f text;
begin
  foreach f in array array['kb_ledger_append_only', 'kb_tax_invoice_immutable', 'kb_audit_append_only'] loop
    if to_regprocedure('public.' || f || '()') is not null then
      execute format('alter function public.%I() set search_path = public, pg_temp', f);
    end if;
  end loop;
end $$;
