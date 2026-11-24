-- 299_table_bookings_specials.sql — table bookings and specials for restaurants
-- (vula/commerce/tables.py, 24 Nov 2026).
--
-- A restaurant sets its sittings (Lunch 12:00–15:00, Dinner 18:00–22:00), how many people it can
-- seat at once and how long a table is kept. A booking takes seats for that long; a booking that
-- would put more people in the room than there are seats is refused. The check and the insert
-- happen in ONE transaction under a per-business lock (book_table below), so two people booking
-- the last table at the same moment can't both get it.
--
-- Idempotent. RLS on every table.

create table if not exists commerce_table_settings (
    tenant_id       text primary key,
    enabled         boolean not null default false,
    sittings        jsonb not null default '[]'::jsonb,   -- [{name, days:[0-6, Mon=0], start:"12:00", end:"15:00"}]
    slot_minutes    integer not null default 30 check (slot_minutes in (15, 30, 60)),
    seating_minutes integer not null default 120 check (seating_minutes between 30 and 480),
    max_covers      integer not null default 40 check (max_covers between 1 and 2000),
    max_party       integer not null default 10 check (max_party between 1 and 100),
    lead_minutes    integer not null default 60 check (lead_minutes between 0 and 10080),
    horizon_days    integer not null default 60 check (horizon_days between 1 and 365),
    note            text,                                  -- shown on the booking page ("Kitchen closes at 21:30")
    updated_at      timestamptz not null default now()
);
alter table commerce_table_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_table_settings;
create policy "tenant_isolation" on commerce_table_settings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_table_bookings (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    ref            text not null,                          -- what the customer quotes: T-4F7K
    starts_at      timestamptz not null,
    ends_at        timestamptz not null,
    party_size     integer not null check (party_size between 1 and 100),
    customer_name  text not null,
    customer_phone text,
    notes          text,
    status         text not null default 'confirmed',      -- confirmed | seated | completed | no_show | cancelled
    channel        text not null default 'web',            -- web | whatsapp | dashboard
    booked_by      text,
    reminded_at    timestamptz,
    cancelled_at   timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (tenant_id, ref)
);
create index if not exists idx_table_bookings_time on commerce_table_bookings (tenant_id, starts_at);
create index if not exists idx_table_bookings_phone on commerce_table_bookings (tenant_id, customer_phone);
alter table commerce_table_bookings enable row level security;
drop policy if exists "tenant_isolation" on commerce_table_bookings;
create policy "tenant_isolation" on commerce_table_bookings
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_specials (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    title       text not null,
    description text,
    price_cents integer check (price_cents is null or price_cents >= 0),
    image_url   text,
    days        integer[] not null default '{}',           -- weekdays it runs (Mon=0); empty = every day
    starts_on   date,
    ends_on     date,
    active      boolean not null default true,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_specials_tenant on commerce_specials (tenant_id, active);
alter table commerce_specials enable row level security;
drop policy if exists "tenant_isolation" on commerce_specials;
create policy "tenant_isolation" on commerce_specials
    using (tenant_id = current_setting('app.tenant_id', true));

-- Take a table: the seats already taken while this party would be sitting, plus this party, must
-- fit in the room. Returns the new booking's id, or null when it doesn't fit.
create or replace function book_table(
    p_tenant_id text, p_ref text, p_starts_at timestamptz, p_ends_at timestamptz, p_party integer,
    p_max_covers integer, p_name text, p_phone text, p_notes text, p_channel text, p_booked_by text)
returns uuid language plpgsql set search_path to 'public' as $$
declare
    v_taken integer;
    v_id uuid;
begin
    perform pg_advisory_xact_lock(hashtext('book_table:' || p_tenant_id));
    select coalesce(sum(party_size), 0) into v_taken
      from commerce_table_bookings
     where tenant_id = p_tenant_id and status in ('confirmed', 'seated')
       and starts_at < p_ends_at and ends_at > p_starts_at;
    if v_taken + p_party > p_max_covers then
        return null;
    end if;
    insert into commerce_table_bookings (tenant_id, ref, starts_at, ends_at, party_size, customer_name,
                                         customer_phone, notes, channel, booked_by)
    values (p_tenant_id, p_ref, p_starts_at, p_ends_at, p_party, p_name, p_phone, p_notes, p_channel, p_booked_by)
    returning id into v_id;
    return v_id;
end;
$$;
revoke all on function book_table(text, text, timestamptz, timestamptz, integer, integer, text, text, text, text, text) from public, anon, authenticated;

comment on table commerce_table_bookings is
    'A restaurant table booking: seats held from starts_at to ends_at. Made only through book_table().';
grant execute on function book_table(text, text, timestamptz, timestamptz, integer, integer, text, text, text, text, text) to service_role;
