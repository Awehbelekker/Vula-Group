-- ============================================================
-- Vula Group — Migration 112: RLS for credential/OAuth tables
-- Part of the security remediation pass (VULA_SECURITY_AND_GAPS_BRIEF.md, verified
-- against current HEAD before writing). These 5 tables store per-tenant OAuth/API
-- credentials and had NO row-level security at all — any caller using the anon key
-- (or a policy-bypassing bug elsewhere) could read another tenant's row. All five
-- have a unique tenant_id column (one connection per tenant), so the standard
-- tenant_isolation pattern already proven in 004_yoco_accounts.sql /
-- 039_payment_providers.sql applies directly, unmodified.
--
-- Encryption note: vula_google_accounts, vula_microsoft_accounts,
-- vula_whatsapp_accounts, vula_clickup_accounts now also encrypt their token
-- columns at the application layer (Fernet, via vula/email_imap/credentials.py's
-- encrypt_secret/decrypt_secret — the same mechanism already used for
-- vula_email_accounts.secret and vula_payment_providers.credentials). RLS here is a
-- second, independent layer — encryption protects the raw bytes, RLS stops one
-- tenant's service context from reading another's row via a misconfigured client.
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_whatsapp_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_whatsapp_accounts;
create policy "tenant_isolation" on vula_whatsapp_accounts
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_google_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_google_accounts;
create policy "tenant_isolation" on vula_google_accounts
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_microsoft_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_microsoft_accounts;
create policy "tenant_isolation" on vula_microsoft_accounts
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_clickup_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_clickup_accounts;
create policy "tenant_isolation" on vula_clickup_accounts
    using (tenant_id = current_setting('app.tenant_id', true));

alter table vula_email_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_email_accounts;
create policy "tenant_isolation" on vula_email_accounts
    using (tenant_id = current_setting('app.tenant_id', true));
