-- 225_whatsapp_numbers.sql — a business can have several WhatsApp numbers, each with a purpose
-- (25 Oct 2026, Ian: "giving them a choice to use the number for clients or internal sales,
-- admin, stores, booking … and what does it look like if the client wants to add numbers").
--
-- Until now vula_whatsapp_accounts held one number per business (tenant_id UNIQUE): connecting a
-- second number replaced the first. Now each row is one number (phone_number_id stays UNIQUE):
--   purpose     shop | bookings | assistant | team (vula/whatsapp_numbers.py); route_mode is kept
--               in step with it and is what inbound routing reads
--   is_primary  the business's main number — messages Vula starts itself go from it; at most one
--               per business
--   label       an optional name for the number ("Stores", "Roland's line")
--
-- Apply AFTER the code that writes by phone_number_id is deployed: the old code upserts on
-- tenant_id, which needs the unique constraint this drops. Adds columns to an existing table
-- (RLS already on, migration 003). Idempotent.

alter table vula_whatsapp_accounts
    add column if not exists purpose text,
    add column if not exists is_primary boolean not null default false,
    add column if not exists label text;

-- Every business has one number today: it is the main one, and its purpose follows its mode.
update vula_whatsapp_accounts set purpose = case route_mode when 'knowledge' then 'assistant' else 'shop' end
    where purpose is null;
update vula_whatsapp_accounts a set is_primary = true
    where not exists (select 1 from vula_whatsapp_accounts b where b.tenant_id = a.tenant_id and b.is_primary)
      and a.id = (select c.id from vula_whatsapp_accounts c where c.tenant_id = a.tenant_id
                  order by (c.status = 'connected') desc, c.created_at limit 1);

alter table vula_whatsapp_accounts drop constraint if exists vula_whatsapp_accounts_tenant_id_key;
create unique index if not exists vula_whatsapp_accounts_one_main
    on vula_whatsapp_accounts (tenant_id) where is_primary;
