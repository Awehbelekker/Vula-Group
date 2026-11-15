-- 258 (written as 255, renumbered before merge — master took 254–256): a deals pipeline for every kind of business (2026-11-10, Ian: "long term vision we need
-- things in place for all types of clients"). One table, one API (vula/pipeline/service.py),
-- one dashboard board. Which stages a business sees depends on its business_type (estate
-- agency: viewing → offer → negotiation → under contract → won/lost; everyone else:
-- lead → quoted → negotiating → won/lost) — the stage list lives in code, the row stores the key.
--
-- Switched on per business by the `pipeline` module (real_estate gets it by default); no
-- existing tenant changes. A deal can point at what it's about (ref_type/ref_id, e.g. a
-- property listing) and at the enquiry it came from.
--
-- Money is integer cents. Idempotent. RLS on, service role only, like every other vula_* table.

create table if not exists vula_deals (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      text not null,
  title          text not null,
  stage          text not null,
  value_cents    bigint,                    -- offer / quote / deal value
  contact_name   text,
  contact_phone  text,                      -- normalised 27XXXXXXXXX
  contact_email  text,
  ref_type       text,                      -- 'property' (vula_properties) | null
  ref_id         uuid,
  enquiry_id     uuid,                      -- vula_property_enquiries, when it started as one
  owner          text,                      -- who's working it (agent / team member name)
  expected_close date,
  notes          text,
  position       integer not null default 0,   -- order within a stage column
  closed_at      timestamptz,               -- set when moved to won/lost
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists vula_deals_tenant_stage_idx on vula_deals (tenant_id, stage, position);
create index if not exists vula_deals_ref_idx on vula_deals (tenant_id, ref_type, ref_id);
alter table vula_deals enable row level security;
