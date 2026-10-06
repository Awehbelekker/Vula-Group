-- ============================================================
-- Vula Group — Tap to Pay (KakEnBetaal), migrations 199-204 combined, in order.
-- Paste into the Supabase SQL editor (staging first if you have one). Safe to re-run: every statement is idempotent.
-- Creates 13 kb_* tables with tenant-isolation RLS and an append-only ledger trigger, then adds a few columns
-- (204: unpaid-bill reminders). No existing Vula table is altered or dropped. The feature stays OFF until a tenant is switched on.
-- Verification query at the very bottom.
-- ============================================================


-- >>>>>>>>>> 199_tap_to_pay.sql
-- ============================================================
-- Vula Group — Migration 199: tap-to-pay (KakEnBetaal) core tables
-- NFC tag -> WhatsApp "PAY <token>" -> tip -> hosted checkout -> slip. Pure rules live in
-- vula/tap/core; these tables are what the (later) service layer persists.
--
-- Conventions (same as 121/039): tenant_id text + tenant_isolation RLS on every table; money is
-- bigint integer cents; idempotency by unique constraints. Customer numbers are never stored in
-- the clear: *_hash is an HMAC for lookup, *_enc the Fernet-encrypted number (POPIA).
-- kb_ledger_lines is APPEND-ONLY (trigger below): refunds add reversing rows, nothing is edited.
-- Party balances (pending/available/paid_out) are derived by summing kb_ledger_lines.
-- Group bills/shares, shifts and payouts are deferred (see plan); the pilot is appointment mode.
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

create table if not exists kb_tags (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    code         text not null,                       -- public code in the tag URL (/t/<code>)
    mode         text not null default 'appointment', -- appointment|counter|table|quick_tip|field
    bound_type   text not null default 'person',      -- person|till|table
    bound_id     text,                                -- vula_team_members.id for a person tag
    allow_open_amount boolean not null default false,
    key_ref      text,                                -- NTAG424 key reference (never the key)
    last_counter bigint not null default 0,           -- SDM replay protection
    status       text not null default 'active',      -- active|disabled
    created_at   timestamptz not null default now(),
    unique (code)
);
create index if not exists idx_kb_tags_tenant on kb_tags (tenant_id);

create table if not exists kb_bills (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    tag_id         uuid references kb_tags(id),
    bill_type      text not null default 'fixed',     -- fixed|open|quick_tip
    status         text not null default 'open',      -- see vula/tap/core/states.py
    description    text,
    subtotal_cents bigint not null check (subtotal_cents >= 0),
    staff_id       text,                              -- who served (revenue split + direct tips)
    customer_hash  text,                              -- HMAC of the addressed number, if any
    customer_enc   text,
    claimed_by_hash text,
    bill_code      text,                              -- 4-digit code for a different-number tap
    code_attempts  int not null default 0,
    code_locked_until timestamptz,
    expires_at     timestamptz,
    created_by     text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_kb_bills_tag on kb_bills (tenant_id, tag_id, status);
-- at most one tag-claimable (unaddressed) live bill per tag; makes the first-tap claim unambiguous
create unique index if not exists uq_kb_bills_one_claimable
    on kb_bills (tag_id) where customer_hash is null and status in ('open', 'claimed') and tag_id is not null;

create table if not exists kb_sessions (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    bill_id         uuid references kb_bills(id),
    payer_hash      text not null,
    payer_enc       text,
    state           text not null default 'claimed',
    bill_cents      bigint not null check (bill_cents >= 0),
    tip_cents       bigint not null default 0 check (tip_cents >= 0),
    total_cents     bigint generated always as (bill_cents + tip_cents) stored,
    checkout_ref    text,                             -- = session id sent to the gateway
    idempotency_key text not null,
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (tenant_id, idempotency_key)
);
create index if not exists idx_kb_sessions_bill on kb_sessions (tenant_id, bill_id);

create table if not exists kb_payments (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    session_id    uuid not null references kb_sessions(id),
    provider      text not null,
    provider_ref  text not null,
    status        text not null,                      -- succeeded|failed|refunded|partially_refunded
    method        text,
    amount_cents  bigint not null check (amount_cents > 0),
    fee_cents     bigint not null default 0,
    refunded_cents bigint not null default 0,
    created_at    timestamptz not null default now(),
    unique (provider, provider_ref)                   -- one payment per provider reference
);
create index if not exists idx_kb_payments_session on kb_payments (tenant_id, session_id);

create table if not exists kb_split_rules (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    scope       text not null default 'default',      -- default|service:<id>|staff:<id>
    staff_share_bp int not null default 0 check (staff_share_bp between 0 and 10000),
    tip_rule    text not null default 'direct' check (tip_rule in ('direct','pool','house_cut')),
    house_cut_bp int not null default 0 check (house_cut_bp between 0 and 10000),
    house_cut_then text not null default 'direct' check (house_cut_then in ('direct','pool')),
    merchant_absorbs_provider_fee boolean not null default false,
    created_at  timestamptz not null default now(),
    unique (tenant_id, scope)
);

create table if not exists kb_ledger_lines (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    payment_id  uuid not null references kb_payments(id),
    kind        text not null check (kind in ('bill','tip','provider_fee','platform_fee')),
    party_id    text not null,                        -- staff id, or 'merchant'
    cents       bigint not null check (cents <> 0),   -- credits > 0, deductions < 0
    reverses    uuid references kb_ledger_lines(id),  -- set on refund reversal rows
    status      text not null default 'pending' check (status in ('pending','available','paid_out')),
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_ledger_payment on kb_ledger_lines (tenant_id, payment_id);
create index if not exists idx_kb_ledger_party on kb_ledger_lines (tenant_id, party_id, status);

create or replace function kb_ledger_append_only() returns trigger language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception 'kb_ledger_lines is append-only';
    end if;
    -- the only permitted change: advancing status pending -> available -> paid_out
    if new.id is distinct from old.id or new.tenant_id is distinct from old.tenant_id
       or new.payment_id is distinct from old.payment_id or new.kind is distinct from old.kind
       or new.party_id is distinct from old.party_id or new.cents is distinct from old.cents
       or new.reverses is distinct from old.reverses then
        raise exception 'kb_ledger_lines is append-only (only status may advance)';
    end if;
    return new;
end $$;
drop trigger if exists trg_kb_ledger_append_only on kb_ledger_lines;
create trigger trg_kb_ledger_append_only before update or delete on kb_ledger_lines
    for each row execute function kb_ledger_append_only();

-- gateway / WhatsApp event idempotency (the generic payments webhook dedupes on invoice status only)
create table if not exists kb_webhook_events (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text,
    source      text not null,                        -- whatsapp|yoco|payfast|peach|...
    event_id    text not null,
    payload_hash text,
    received_at timestamptz not null default now(),
    processed_at timestamptz,
    unique (source, event_id)
);

create table if not exists kb_reminders (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    bill_id     uuid not null references kb_bills(id),
    session_id  uuid references kb_sessions(id),
    seq         int not null check (seq between 1 and 3),
    template    text,
    status      text not null default 'sent',
    sent_at     timestamptz not null default now(),
    unique (bill_id, seq)                             -- never more than 3, never the same one twice
);

-- ── RLS (tenant_isolation, same shape as 121) ────────────────────────────────────────────────
alter table kb_tags enable row level security;
alter table kb_bills enable row level security;
alter table kb_sessions enable row level security;
alter table kb_payments enable row level security;
alter table kb_split_rules enable row level security;
alter table kb_ledger_lines enable row level security;
alter table kb_webhook_events enable row level security;
alter table kb_reminders enable row level security;

do $$
declare t text;
begin
    foreach t in array array['kb_tags','kb_bills','kb_sessions','kb_payments','kb_split_rules',
                             'kb_ledger_lines','kb_webhook_events','kb_reminders'] loop
        execute format('drop policy if exists "tenant_isolation" on %I', t);
        execute format('create policy "tenant_isolation" on %I using (tenant_id = current_setting(''app.tenant_id'', true))', t);
    end loop;
end $$;

-- >>>>>>>>>> 200_tap_claim_tokens.sql
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

-- >>>>>>>>>> 201_tap_settings.sql
-- ============================================================
-- Vula Group — Migration 201: per-tenant tap-to-pay switch + test-payment flag
-- Replaces the TAP_TO_PAY_TENANTS env allowlist for self-serve setup (the env list still works as
-- an operator override). mode: off -> testing (R5 test payment in flight) -> live. A tenant can only
-- go live after a test payment has actually been confirmed (tested_at).
-- kb_bills.is_test: the R5 setup test bill — skipped by the ledger and the staff alert.
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

create table if not exists kb_settings (
    tenant_id   text primary key,
    mode        text not null default 'off' check (mode in ('off', 'testing', 'live')),
    tested_at   timestamptz,
    updated_at  timestamptz not null default now()
);

alter table kb_settings enable row level security;
drop policy if exists "tenant_isolation" on kb_settings;
create policy "tenant_isolation" on kb_settings
    using (tenant_id = current_setting('app.tenant_id', true));

alter table kb_bills add column if not exists is_test boolean not null default false;

-- >>>>>>>>>> 202_tap_app.sql
-- ============================================================
-- Vula Group — Migration 202: tap-to-pay coach app (PWA) — devices, enrolment codes, push
-- Coaches/cashiers use the /pay/ PWA. No Supabase login: the owner issues a one-time 6-digit
-- enrolment code for a team member (kb_enrol_codes, 10 min, 5 wrong tries burns it); the coach
-- enters it with their WhatsApp number and picks a PIN, which registers THIS phone (kb_devices).
-- Afterwards: device token (long-lived, stored hashed) + PIN -> short-lived signed access token.
-- 5 wrong PINs lock the device for 15 minutes; revoking a device takes effect on the next request.
-- kb_push_subs: Web Push subscriptions (iOS needs the PWA installed to the home screen first).
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

create table if not exists kb_enrol_codes (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    member_id   text not null,
    code_hash   text not null,
    attempts    int not null default 0,
    expires_at  timestamptz not null,
    used_at     timestamptz,
    created_by  text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_enrol_member on kb_enrol_codes (tenant_id, member_id);

create table if not exists kb_devices (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    member_id     text not null,
    label         text,
    token_hash    text not null unique,
    pin_salt      text not null,
    pin_hash      text not null,
    failed_pins   int not null default 0,
    locked_until  timestamptz,
    revoked_at    timestamptz,
    last_seen_at  timestamptz,
    created_at    timestamptz not null default now()
);
create index if not exists idx_kb_devices_tenant on kb_devices (tenant_id, member_id);

create table if not exists kb_push_subs (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    member_id   text not null,
    device_id   uuid references kb_devices(id) on delete cascade,
    endpoint    text not null unique,
    p256dh      text not null,
    auth        text not null,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_push_member on kb_push_subs (tenant_id, member_id);

alter table kb_enrol_codes enable row level security;
alter table kb_devices enable row level security;
alter table kb_push_subs enable row level security;

do $$
declare t text;
begin
    foreach t in array array['kb_enrol_codes','kb_devices','kb_push_subs'] loop
        execute format('drop policy if exists "tenant_isolation" on %I', t);
        execute format('create policy "tenant_isolation" on %I using (tenant_id = current_setting(''app.tenant_id'', true))', t);
    end loop;
end $$;

-- >>>>>>>>>> 203_tap_receipts.sql
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

-- >>>>>>>>>> 204_tap_reminders.sql
-- ============================================================
-- Vula Group — Migration 204: tap-to-pay unpaid-bill reminders
-- A customer who saw their total and then left (or whose payment failed) leaves the bill 'abandoned'.
-- The sweeper then sends up to 3 reminders (10 min, next morning, day 3), 08:00-20:00 SAST only, at
-- most one per day, then the bill becomes 'needs_follow_up' for the owner. Every reminder is logged in
-- kb_reminders. A manual "resend" by the owner is logged too (kind='manual', seq 0).
--   kb_settings.reminders_max     0..3 reminders per bill; 0 = off (straight to needs_follow_up)
--   kb_bills.abandoned_at         when the customer left (drives the schedule)
--   kb_bills.last_session_id      the session whose payer/amount/tip the reminders reuse
--   kb_bills.closed_reason        why it was closed by hand: cash | eft | other | written_off
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

alter table kb_settings add column if not exists reminders_max int not null default 3
    check (reminders_max between 0 and 3);

alter table kb_bills add column if not exists abandoned_at timestamptz;
alter table kb_bills add column if not exists last_session_id uuid;
alter table kb_bills add column if not exists closed_reason text;
create index if not exists idx_kb_bills_unpaid on kb_bills (tenant_id, status) where status in ('abandoned', 'needs_follow_up');

alter table kb_reminders add column if not exists kind text not null default 'auto';   -- auto | manual
alter table kb_reminders add column if not exists channel text;                        -- text | template | none
-- the (bill_id, seq) uniqueness now applies to automatic reminders only, so owners can resend by hand
alter table kb_reminders drop constraint if exists kb_reminders_bill_id_seq_key;
alter table kb_reminders drop constraint if exists kb_reminders_seq_check;
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'kb_reminders_seq_range') then
        alter table kb_reminders add constraint kb_reminders_seq_range check (seq between 0 and 3);
    end if;
end $$;
create unique index if not exists uq_kb_reminders_auto on kb_reminders (bill_id, seq) where kind = 'auto';

-- >>>>>>>>>> verification (should return 13 tables, all with RLS on)
select c.relname as table_name, c.relrowsecurity as rls_on,
       (select count(*) from pg_policies p where p.tablename = c.relname) as policies
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'kb\_%'
order by c.relname;
