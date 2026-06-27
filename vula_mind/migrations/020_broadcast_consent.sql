-- 020_broadcast_consent.sql — broadcast delivery analytics + POPIA consent/suppression.
--
-- Makes WhatsApp broadcasting measurable (per-message delivery/read status, rolled up
-- into commerce_broadcast_logs) and compliant (implied opt-in on first inbound; persistent
-- opt-out suppression honoured before every send).

-- One row per outbound broadcast message → links the Meta wamid to its broadcast so the
-- status webhook (sent/delivered/read/failed) can update engagement counts.
create table if not exists commerce_broadcast_recipients (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    broadcast_id uuid not null,
    phone        text not null,
    wamid        text unique,                       -- Meta message id from the send response
    status       text not null default 'sent',      -- sent | delivered | read | failed
    error        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_bcast_recipients_broadcast on commerce_broadcast_recipients (broadcast_id);
create index if not exists idx_bcast_recipients_wamid on commerce_broadcast_recipients (wamid);

-- Marketing consent / suppression. Implied opt-in on first inbound; opt-out persists as the
-- do-not-contact list (kept even after PII deletion) and is excluded from every broadcast.
create table if not exists commerce_consent (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    phone      text not null,
    status     text not null default 'opted_in',    -- opted_in | opted_out
    source     text,                                 -- inbound | stop_keyword | dashboard
    updated_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (tenant_id, phone)
);
create index if not exists idx_commerce_consent_tenant on commerce_consent (tenant_id, status);

-- failed_count may not exist on the broadcast log yet (mig 006 had delivered/read only).
alter table commerce_broadcast_logs add column if not exists failed_count int default 0;

-- Pre-existing bug: commerce_broadcast_logs has an updated_at TRIGGER but no
-- updated_at COLUMN, so every UPDATE to it 400s ("record new has no field updated_at").
-- Add the column the trigger expects.
alter table commerce_broadcast_logs add column if not exists updated_at timestamptz default now();
