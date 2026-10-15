-- 212_wa_login.sql — log in to the dashboard with WhatsApp (15 Oct 2026, Ian: "a simpler way to
-- log in, as we do with WhatsApp — a QR code, or a PIN sent to the staff member's WhatsApp").
--
-- One row per login attempt. Two ways in, both proving the person holds the team member's
-- WhatsApp:
--   qr    the login page shows a QR / button that opens WhatsApp with "Login K7F4QX" to the
--         business's number; Vula sees it came from a team member and asks them to Approve on
--         WhatsApp (showing which browser), then the page gets its session
--   code  the person types their WhatsApp number; Vula sends a 6-digit code to it; they type it
--
-- Secrets are stored hashed: the browser's polling secret and the 6-digit code. The server
-- (service role) is the only reader/writer; RLS on with no policies. Idempotent; nothing dropped.

create table if not exists vula_wa_login_requests (
    id              uuid primary key default gen_random_uuid(),
    kind            text not null check (kind in ('qr', 'code')),
    tenant_id       text,                      -- known from the login address, or from the team member
    login_code      text,                      -- qr: what the person sends ("K7F4QX"), unique while pending
    otp_hash        text,                      -- code: sha256 of the 6 digits + request id
    secret_hash     text not null,             -- sha256 of the secret only the requesting browser holds
    phone           text,                      -- the team member's WhatsApp (digits)
    status          text not null default 'pending'
                    check (status in ('pending', 'awaiting_approval', 'approved', 'denied', 'expired', 'used')),
    attempts        integer not null default 0,
    device          text,                      -- "Chrome on Windows" — shown on the Approve message
    ip              text,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    decided_at      timestamptz,
    used_at         timestamptz
);
create unique index if not exists uq_wa_login_code_pending
    on vula_wa_login_requests (login_code) where status in ('pending', 'awaiting_approval');
create index if not exists idx_wa_login_phone_recent on vula_wa_login_requests (phone, created_at desc);
alter table vula_wa_login_requests enable row level security;
