-- ============================================================
-- Vula Group — Migration 203: tap-to-pay receipt links
-- The customer's WhatsApp slip links to a public receipt page (/r/<token>) that "prints" the slip.
-- The token is an HMAC of (payment id, nonce) — nothing secret is stored, the link is unguessable
-- (128-bit MAC), and an owner can revoke it (kb_payments.receipt_revoked_at) or re-issue a new one
-- by bumping receipt_nonce, which kills every older link at once.
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================
alter table kb_payments add column if not exists receipt_nonce int not null default 0;
alter table kb_payments add column if not exists receipt_revoked_at timestamptz;
