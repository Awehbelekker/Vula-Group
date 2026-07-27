-- 109_documents_customer_linkage.sql — filed documents had no way to associate with a customer
-- at all (only project/supplier), making "show me everything filed for this client" impossible.
-- Phone, not a FK id, matching the CRM's own phone-keyed customer identity (_aggregate_customers
-- unions orders/sessions/contacts by normalized phone — there's no separate customers table with
-- a stable id to reference). `category` already exists (migration 015) — the new "just store this"
-- media upload path reuses it with the value 'media', no schema change needed for that part.

alter table vula_filed_documents
    add column if not exists customer_phone text;

create index if not exists idx_filed_documents_customer on vula_filed_documents (tenant_id, customer_phone);
