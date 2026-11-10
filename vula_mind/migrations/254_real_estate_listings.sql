-- 254: property listings for real-estate businesses (2026-11-10, Ian: "can we build this for our
-- real estate tenants"). A real-estate tenant (business_type 'real_estate', vula/api/tenants.py)
-- lists properties for sale or to rent, shows its agents, and takes enquiries ("I'd like to view
-- this") from its website. Viewings themselves reuse the existing bookings system
-- (commerce_services / commerce_bookings) — no separate calendar here.
--
-- Money is integer cents, like everywhere else. Idempotent. RLS on, service role only, like every
-- other vula_* table: the API (vula/listings/service.py) scopes every query by tenant_id.

create table if not exists vula_property_agents (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    text not null,
  name         text not null,
  title        text,                       -- "Principal agent", "Rentals"
  whatsapp     text,                       -- enquiries on this agent's listings go here too
  email        text,
  photo_url    text,
  bio          text,
  ppra_ffc     text,                       -- the agent's PPRA Fidelity Fund Certificate number
  active       boolean not null default true,
  sort         integer not null default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists vula_property_agents_tenant_idx on vula_property_agents (tenant_id, active);
alter table vula_property_agents enable row level security;

create table if not exists vula_properties (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       text not null,
  slug            text not null,
  title           text not null,
  listing_type    text not null default 'sale'   check (listing_type in ('sale', 'rent')),
  status          text not null default 'draft'
                  check (status in ('draft', 'active', 'under_offer', 'sold', 'let', 'archived')),
  property_type   text,                    -- house | apartment | townhouse | land | commercial | farm
  price_cents     bigint,                  -- asking price, or monthly rent when listing_type='rent'
  price_on_request boolean not null default false,
  bedrooms        numeric(4,1),            -- 2.5 bedrooms is a real SA listing thing
  bathrooms       numeric(4,1),
  garages         integer,
  parking         integer,
  floor_m2        integer,
  erf_m2          integer,
  suburb          text,
  city            text,
  province        text,
  address         text,                    -- shown only when show_address
  show_address    boolean not null default false,
  latitude        double precision,
  longitude       double precision,
  description     text,
  features        jsonb not null default '[]'::jsonb,   -- ["Pool", "Pet friendly", "Solar"]
  images          jsonb not null default '[]'::jsonb,   -- public image URLs, first = cover
  levies_cents    bigint,                  -- monthly levies (sectional title / estates)
  rates_cents     bigint,                  -- monthly municipal rates
  available_from  date,                    -- rentals
  agent_id        uuid references vula_property_agents(id) on delete set null,
  featured        boolean not null default false,
  sort            integer not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, slug)
);
create index if not exists vula_properties_tenant_status_idx on vula_properties (tenant_id, status);
alter table vula_properties enable row level security;

create table if not exists vula_property_enquiries (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    text not null,
  property_id  uuid references vula_properties(id) on delete set null,
  name         text not null,
  whatsapp     text,
  email        text,
  message      text,
  wants_viewing boolean not null default false,
  status       text not null default 'new' check (status in ('new', 'contacted', 'viewing', 'closed')),
  ip           text,
  created_at   timestamptz not null default now()
);
create index if not exists vula_property_enquiries_tenant_idx
  on vula_property_enquiries (tenant_id, created_at desc);
alter table vula_property_enquiries enable row level security;
