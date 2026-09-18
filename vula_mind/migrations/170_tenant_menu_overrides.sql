-- 170_tenant_menu_overrides.sql — per-tenant override for the WhatsApp staff capability menu's
-- example commands (_STAFF_MENU_ALWAYS_ON/_STAFF_MENU_BY_MODULE, vula/api/whatsapp.py). Those
-- were hardcoded Python constants — changing a tenant's menu copy required a code deploy.
-- Backend-only this pass (go-live readiness audit's confirmed scope): editable via SQL/master
-- tooling, no dashboard UI yet — revisit once there's real demand for one.
--
-- menu_key matches the existing keys in _STAFF_MENU_ALWAYS_ON/_STAFF_MENU_BY_MODULE (e.g.
-- "sales", "invoices"), not the module name — same key _send_staff_capability_menu already
-- prefixes onto its "admin_example:{key}" interactive-list row ids.

create table if not exists vula_tenant_menu_overrides (
    tenant_id  text not null,
    menu_key   text not null,
    title      text,
    command    text,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, menu_key)
);

alter table vula_tenant_menu_overrides enable row level security;
drop policy if exists "tenant_isolation" on vula_tenant_menu_overrides;
create policy "tenant_isolation" on vula_tenant_menu_overrides
    using (tenant_id = current_setting('app.tenant_id', true));
