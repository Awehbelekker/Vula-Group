-- 102_supplier_linking.sql — schema foundation for verified document intake + supplier matching
-- (store-admin-reconciliation follow-on: "does a quotation generate the supplier and file
-- everything under it?"). Four independent, idempotent pieces:

-- 1. Close real schema drift: commerce_suppliers.tax_id / .layout_signature are already read and
-- written by vula/commerce/service.py (upsert_supplier/match_supplier) and the dashboard's
-- supplier form (VulaMerchantAdmin.jsx), but no migration file in this repo ever added them —
-- they exist today only via undocumented manual Supabase changes. Formalize them.
alter table commerce_suppliers
    add column if not exists tax_id text,
    add column if not exists layout_signature text;

create index if not exists idx_suppliers_tax_id
    on commerce_suppliers(tenant_id, tax_id) where tax_id is not null;

-- 2. supplier_id links — nullable, on delete set null, indexed. The existing free-text
-- supplier/vendor columns are untouched (they stay the human-readable fallback / what was
-- literally printed on the document); supplier_id is the new dedup/grouping link.
--
-- Type note: commerce_suppliers.id is TEXT in production (created via the older
-- db/migrations/002_commerce_admin_tables.sql, gen_random_uuid()::text) — NOT uuid like the
-- other three tables here (confirmed live via PostgREST's schema, not assumed). Every FK to it
-- below is therefore `text`, even though it points to what happens to be a uuid-shaped value.
alter table commerce_invoices
    add column if not exists supplier_id text references commerce_suppliers(id) on delete set null;
create index if not exists idx_invoices_supplier on commerce_invoices(tenant_id, supplier_id);

alter table commerce_expenses
    add column if not exists supplier_id text references commerce_suppliers(id) on delete set null;
create index if not exists idx_expenses_supplier on commerce_expenses(tenant_id, supplier_id);

alter table vula_filed_documents
    add column if not exists supplier_id text references commerce_suppliers(id) on delete set null;
create index if not exists idx_filed_documents_supplier on vula_filed_documents(tenant_id, supplier_id);

-- 3. Bridge a filed document to the real commerce_invoices row it gets promoted into, plus
-- confidence telemetry that's actually queryable (today's scan_confidence on commerce_invoices
-- is write-only — nothing reads it back for review).
alter table vula_filed_documents
    add column if not exists commerce_invoice_id uuid references commerce_invoices(id) on delete set null,
    add column if not exists match_confidence numeric,
    add column if not exists supplier_match_tier text,       -- tax_id | exact_name | fuzzy_name | layout | none
    add column if not exists needs_review boolean not null default false;

create index if not exists idx_filed_documents_invoice on vula_filed_documents(tenant_id, commerce_invoice_id);

-- 4. Widen the doc_type CHECK so a delivery note can be committed through the same shared path
-- as invoices/quotes (previously only invoice|quote|proforma|credit_note were allowed).
alter table commerce_invoices drop constraint if exists commerce_invoices_doc_type_chk;
alter table commerce_invoices add constraint commerce_invoices_doc_type_chk
    check (doc_type in ('invoice', 'quote', 'proforma', 'credit_note', 'delivery_note'));
