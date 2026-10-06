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
