-- ============================================================
-- Vula Group — Migration 200: tap-to-pay one-time claim tokens + PayFast payment id
-- A tap on an NFC tag/QR mints a short-lived, single-use token that is put in the pre-filled
-- WhatsApp message ("PAY <token>"). Only the SHA-256 of the token is stored, so a DB read can't
-- be replayed. consume = one conditional UPDATE (used_at is null and not expired).
-- kb_payments.pf_payment_id: the gateway's own payment id (PayFast pf_payment_id), needed for
-- refunds and ITN dedupe; provider_ref stays our unique per-payment key.
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

create table if not exists kb_claim_tokens (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    tag_id      uuid references kb_tags(id),
    bill_id     uuid references kb_bills(id),
    token_hash  text not null unique,
    expires_at  timestamptz not null,
    used_at     timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_claim_tokens_tenant on kb_claim_tokens (tenant_id, expires_at);

alter table kb_claim_tokens enable row level security;
drop policy if exists "tenant_isolation" on kb_claim_tokens;
create policy "tenant_isolation" on kb_claim_tokens
    using (tenant_id = current_setting('app.tenant_id', true));

alter table kb_payments add column if not exists pf_payment_id text;
alter table kb_sessions add column if not exists pay_nonce_hash text;   -- one-time /pay redirect nonce
alter table kb_sessions add column if not exists expecting text;        -- typed reply awaited: custom_tip|amount|quick_tip|bill_code
alter table kb_sessions add column if not exists staff_ref text;         -- staff a quick-tip / open-amount session pays (from the tag binding)
