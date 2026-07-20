-- 074_delivery_geo.sql — Facebook-Marketplace-style delivery location: the tenant drops a pin
-- on their shop and sets a delivery radius (km). Complements the named-areas list from 070:
-- radius gives a deterministic yes/no for shared location pins and geocodable addresses;
-- the areas list stays as the human-readable grounding for the assistant.

alter table commerce_order_settings
    add column if not exists origin_lat double precision,
    add column if not exists origin_lng double precision,
    add column if not exists origin_label text,               -- e.g. "Table View, Cape Town"
    add column if not exists delivery_radius_km double precision;  -- NULL = radius not used

-- Geocode cache (Nominatim/OSM free tier is rate-limited to ~1 req/s — cache aggressively).
create table if not exists commerce_geo_cache (
    query      text primary key,          -- normalized lowercase query
    lat        double precision,
    lng        double precision,
    label      text,
    created_at timestamptz not null default now()
);
