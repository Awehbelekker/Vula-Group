-- ============================================================
-- Vula Group — Migration 090: RLS gap fix for commerce_product_variants
-- Migration 087 created commerce_product_variants without row-level security —
-- every other tenant-scoped table in this schema enables RLS + a tenant_isolation
-- policy (see commerce_products, commerce_invoice_items, vula_project_cards, etc.);
-- this table was the one exception. The backend's service-role key already bypasses
-- RLS for normal API traffic, so this is defense-in-depth, not a live exploit fix —
-- but it should match convention like every other table here. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_product_variants enable row level security;
drop policy if exists "tenant_isolation" on commerce_product_variants;
create policy "tenant_isolation" on commerce_product_variants
    using (tenant_id = current_setting('app.tenant_id', true));
