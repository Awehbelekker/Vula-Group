-- 296_couriers.sql — book a courier from an order (23 Nov 2026).
--
-- A business connects its own courier account (The Courier Guy, PUDO lockers), the way it connects
-- its own payment gateway: the courier bills the business, Vula never holds the delivery money.
--   vula_courier_providers     one row per connected courier; the API key is Fernet-wrapped in
--                              `credentials` like vula_payment_providers; `webhook_token` is the
--                              secret part of the address the courier calls with tracking news.
--   commerce_courier_settings  where parcels are collected from, who hands them over, and the
--                              parcel size used when an order's own isn't known.
--   commerce_shipments         one row per booking, written BEFORE the courier is called
--                              (status 'booking') so a booking that half-happened leaves a trace.
--                              An order has at most one live shipment — the partial unique index
--                              is what stops two people (or a double click) booking it twice.
-- Written only by the server. Idempotent.

create table if not exists vula_courier_providers (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    provider      text not null,                       -- tcg | pudo
    credentials   jsonb not null default '{}'::jsonb,  -- {"_enc": "fernet:..."} (secret)
    mode          text not null default 'live',        -- live | test
    is_default    boolean not null default false,
    active        boolean not null default true,
    webhook_token text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (tenant_id, provider)
);
alter table vula_courier_providers enable row level security;
drop policy if exists "tenant_isolation" on vula_courier_providers;
create policy "tenant_isolation" on vula_courier_providers
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_courier_settings (
    tenant_id          text primary key,
    collection_address jsonb,     -- {company, street_address, local_area, city, zone, code, lat, lng, type}
    collection_contact jsonb,     -- {name, mobile_number, email}
    default_parcel     jsonb,     -- {length_cm, width_cm, height_cm, weight_kg}
    collection_point   jsonb,     -- PUDO: the locker parcels are dropped at, {id, name}; null = collected at the door
    updated_at         timestamptz not null default now()
);
alter table commerce_courier_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_courier_settings;
create policy "tenant_isolation" on commerce_courier_settings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_shipments (
    id                   uuid primary key default gen_random_uuid(),
    tenant_id            text not null,
    order_id             uuid not null,
    provider             text not null,
    mode                 text not null default 'live',
    provider_shipment_id text,
    tracking_reference   text,
    service_code         text,
    service_name         text,
    quoted_cents         integer,          -- the price shown to the person who booked
    charged_cents        integer,          -- what the courier said it charged, when it says
    status               text not null default 'booking',   -- booking | failed | the courier's own status
    delivery_kind        text not null default 'door',      -- door | pickup_point
    pickup_point_id      text,
    pickup_point_name    text,
    collection_point_id  text,
    delivery_address     jsonb,
    parcels              jsonb,
    events               jsonb not null default '[]'::jsonb,
    error                text,
    verified_at          timestamptz,      -- when the booking was read back from the courier
    open                 boolean not null default true,     -- false once delivered, cancelled, failed or returned
    problem_notified     text,             -- the last problem status the team was told about
    booked_by            text,
    last_checked_at      timestamptz,
    cancelled_at         timestamptz,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);
create index if not exists idx_shipments_order on commerce_shipments (tenant_id, order_id);
create index if not exists idx_shipments_reference on commerce_shipments (tenant_id, provider, tracking_reference);
create index if not exists idx_shipments_open on commerce_shipments (last_checked_at) where open;
create unique index if not exists uq_shipments_one_live_per_order on commerce_shipments (tenant_id, order_id)
    where status not in ('cancelled', 'failed');
alter table commerce_shipments enable row level security;
drop policy if exists "tenant_isolation" on commerce_shipments;
create policy "tenant_isolation" on commerce_shipments
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_shipments is
    'A courier booking for an order. One live row per order; cancelled and failed ones are kept.';
