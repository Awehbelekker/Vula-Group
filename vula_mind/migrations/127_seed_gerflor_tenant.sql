-- 127_seed_gerflor_tenant.sql — Ian's own sales-rep tenant for his Gerflor Western Cape role.
-- Scoped to just Ian for now (one tenant, one registered sales rep) — same underlying
-- sales-rep-within-company feature (migration 110 + core/skills/commerce_admin.py) already
-- supports adding more reps later exactly the same way, if the team grows into this.
--
-- Still needed after this runs, before it's actually usable over WhatsApp (not something SQL
-- can do — needs Ian's own action via the dashboard):
--   1. Connect a WhatsApp Business number for 'gerflor' (WhatsApp Embedded Signup, same flow
--      as any other tenant) — inserts a real row into vula_whatsapp_accounts.
--   2. Set that row's route_mode = 'knowledge' (matches digg-demo's pattern — the sales-rep
--      gate in vula/api/whatsapp.py only fires on knowledge-mode/tenant-resolved lines).
--   3. Update vula_tenant_config.wa_phone_number_id below to match the connected number.

insert into vula_tenant_config
    (tenant_id, display_name, business_type, modules, plan, status)
values
  ('gerflor', 'Gerflor — Western Cape Sales', 'trades',
   '["crm","followups"]'::jsonb, 'starter', 'active')
on conflict (tenant_id) do update set
   display_name = excluded.display_name, business_type = excluded.business_type,
   modules = excluded.modules, plan = excluded.plan, status = excluded.status,
   updated_at = now();

-- Ian's own phone, registered as the tenant's sales rep (role gates the scoped tool set —
-- see core/skills/commerce_admin.py's _REP_TOOL_SPECS). Digits only, 0-prefix normalised to 27,
-- matching the same normalisation vula/api/team.py and vula/api/whatsapp.py both apply.
insert into vula_team_members
    (tenant_id, name, whatsapp, role, active)
values
  ('gerflor', 'Ian', '27645755210', 'sales_rep', true)
on conflict (tenant_id, whatsapp) do update set
   name = excluded.name, role = excluded.role, active = excluded.active,
   updated_at = now();
