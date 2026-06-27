-- 017_qs_rates.sql — per-tenant Quantity Surveying rate library.
--
-- DIGG's own rates (e.g. breeze-block walling, acoustic panels, fees) so cost
-- estimates are computed from real, owned figures — never market rates guessed by
-- the model. The calculations skill looks rates up here via lookup_rate.

create table if not exists vula_qs_rates (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    code        text,                         -- optional short ref, e.g. BRICK-DBL
    description text not null,                -- "Double-skin clay brick wall, plastered both sides"
    unit        text not null default 'item', -- m2 | m3 | m | no | kg | item | sum
    rate        numeric not null,             -- ZAR per unit
    category    text,                         -- Masonry | Concrete | Finishes | Prelims | …
    source      text,                         -- "DIGG 2024" | supplier quote ref
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_vula_qs_rates_tenant on vula_qs_rates (tenant_id, category);
