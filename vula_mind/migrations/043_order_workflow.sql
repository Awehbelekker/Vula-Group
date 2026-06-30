-- ============================================================
-- Vula Group — Migration 043: per-tenant order workflow
-- Optional approval gate before fulfilment + configurable dispatch (WhatsApp / email).
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_order_settings (
    tenant_id            text primary key,
    require_approval     boolean not null default false,
    dispatch_channel     text not null default 'whatsapp',  -- whatsapp | email | both | none
    fulfillment_email    text,
    fulfillment_whatsapp text,
    updated_at           timestamptz not null default now()
);

alter table commerce_order_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_order_settings;
create policy "tenant_isolation" on commerce_order_settings
    using (tenant_id = current_setting('app.tenant_id', true));

-- Off the Hook: Stacy approves orders, fulfilment goes to her WhatsApp
insert into commerce_order_settings (tenant_id, require_approval, dispatch_channel, fulfillment_whatsapp)
values ('off-the-hook', true, 'whatsapp', '27722684085')
on conflict (tenant_id) do nothing;
