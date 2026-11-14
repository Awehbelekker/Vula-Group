-- 272_trade_buildups.sql — how one unit of building work is made up, confirmed once, reused.
--
-- 14 Nov 2026 (trade costing, phase 2; Ian: "Vula learns, and later only looks up"). A build-up
-- is what goes into ONE unit of an item of work — per m² of one-brick wall: so many bricks, so
-- much mortar cement and sand, brickforce, labour hours — with the waste allowed on each line.
-- Vula researches it once from cited sources (only quantities printed in a cited page are kept;
-- the rest are listed as unknown), an owner or manager confirms it, and every later estimate
-- looks it up. A change makes a new version. Quantities only — never prices.
--
-- Ian chose to share confirmed build-ups Vula-wide: another business can use one as it is
-- (shown as "confirmed by another Vula business", never naming it) or confirm its own copy.
-- Idempotent.

create table if not exists vula_buildups (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    trade         text not null,
    item          text not null,
    item_key      text not null,
    unit          text not null,
    spec          jsonb not null default '{}'::jsonb,
    status        text not null default 'proposed' check (status in ('proposed', 'confirmed', 'retired')),
    version       integer not null default 1,
    sources       jsonb not null default '[]'::jsonb,
    unknown       jsonb not null default '[]'::jsonb,
    shared        boolean not null default true,
    based_on      uuid,
    created_by    text,
    created_at    timestamptz not null default now(),
    confirmed_by  text,
    confirmed_at  timestamptz
);
create index if not exists vula_buildups_tenant_key_idx on vula_buildups (tenant_id, item_key, status);
create index if not exists vula_buildups_shared_idx on vula_buildups (item_key, status) where shared;
alter table vula_buildups enable row level security;

create table if not exists vula_buildup_lines (
    id            uuid primary key default gen_random_uuid(),
    buildup_id    uuid not null references vula_buildups (id) on delete cascade,
    tenant_id     text not null,
    position      integer not null default 0,
    component     text not null,
    label         text not null,
    kind          text not null check (kind in ('material', 'labour', 'plant')),
    qty_per_unit  numeric(14, 4) not null check (qty_per_unit > 0),
    unit          text not null,
    waste_pct     numeric(6, 2) not null default 0 check (waste_pct >= 0 and waste_pct < 100),
    note          text,
    source_ref    text
);
create index if not exists vula_buildup_lines_buildup_idx on vula_buildup_lines (buildup_id, position);
alter table vula_buildup_lines enable row level security;
