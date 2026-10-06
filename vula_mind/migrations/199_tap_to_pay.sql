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
