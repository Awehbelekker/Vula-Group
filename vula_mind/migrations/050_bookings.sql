-- ============================================================
-- Vula Group — Migration 050: Bookings / Appointments
-- A calendar-slot booking module for health/wellness/services/salons: bookable service
-- types, per-tenant availability rules, and the bookings themselves. Powers dashboard
-- scheduling, storefront "book now", and WhatsApp ("book me Tue 2pm").
-- Run in Supabase SQL editor.
-- ============================================================

-- 1. Bookable service types (a haircut, a consult, a massage) — name, how long, price.
create table if not exists commerce_services (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    name         text not null,
    description  text,
    duration_min int  not null default 30,      -- slot length in minutes
    price_cents  int  not null default 0,        -- 0 = free / price on the day
    active       bool not null default true,
    sort         int  not null default 0,
    created_at   timestamptz not null default now()
);
create index if not exists idx_commerce_services_tenant on commerce_services (tenant_id, active, sort);

-- 2. Per-tenant availability rules (when you're open + how bookings are spaced).
create table if not exists commerce_booking_settings (
    tenant_id    text primary key,
    timezone     text not null default 'Africa/Johannesburg',
    slot_minutes int  not null default 30,       -- granularity of the slot grid
    workdays     int[] not null default '{1,2,3,4,5}',  -- ISO weekdays Mon=1 … Sun=7
    open_time    text not null default '09:00',   -- HH:MM local
    close_time   text not null default '17:00',
    buffer_min   int  not null default 0,         -- gap forced between bookings
    lead_hours   int  not null default 2,         -- minimum notice before a slot
    horizon_days int  not null default 30,        -- how far ahead bookings are allowed
    updated_at   timestamptz not null default now()
);

-- 3. The bookings.
create table if not exists commerce_bookings (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    service_id     uuid,
    service_name   text,                          -- denormalised so history survives service edits
    customer_name  text,
    customer_phone text,
    customer_email text,
    start_at       timestamptz not null,
    end_at         timestamptz not null,
    status         text not null default 'confirmed',  -- pending|confirmed|cancelled|completed|no_show
    notes          text,
    channel        text not null default 'web',   -- web | whatsapp | dashboard
    reminder_sent  bool not null default false,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_commerce_bookings_slot on commerce_bookings (tenant_id, start_at);
create index if not exists idx_commerce_bookings_status on commerce_bookings (tenant_id, status, start_at);
create index if not exists idx_commerce_bookings_phone on commerce_bookings (tenant_id, customer_phone);

-- RLS: tenant isolation (service key bypasses for backend jobs).
alter table commerce_services         enable row level security;
alter table commerce_booking_settings enable row level security;
alter table commerce_bookings         enable row level security;

drop policy if exists "tenant_isolation" on commerce_services;
create policy "tenant_isolation" on commerce_services
    using (tenant_id = current_setting('app.tenant_id', true));
drop policy if exists "tenant_isolation" on commerce_booking_settings;
create policy "tenant_isolation" on commerce_booking_settings
    using (tenant_id = current_setting('app.tenant_id', true));
drop policy if exists "tenant_isolation" on commerce_bookings;
create policy "tenant_isolation" on commerce_bookings
    using (tenant_id = current_setting('app.tenant_id', true));
