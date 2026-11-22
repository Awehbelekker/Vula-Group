-- 293_bank_line_documents.sql — one bank payment that paid several supplier bills (20 Nov 2026).
--
-- A bank line can carry one matched_invoice_id. DIGG paid two Belekkie Skips bills (R5,600 + R2,800)
-- with one R8,400 transfer: tied to one bill, the other stayed "paid with no bank line" (in transit)
-- and the first looked overpaid. Each row here says "this much of this bank line paid this bill"; a
-- line with rows here is read through them (vula/commerce/books_review.py pay_bill, books_rebuild's
-- money in transit). Written only by the server. Idempotent.

create table if not exists commerce_bank_line_documents (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    txn_id        uuid not null,
    invoice_id    uuid not null,
    amount_cents  bigint not null check (amount_cents > 0),
    created_by    text,
    created_at    timestamptz not null default now(),
    unique (tenant_id, txn_id, invoice_id)
);
create index if not exists commerce_bank_line_documents_invoice
    on commerce_bank_line_documents (tenant_id, invoice_id);
alter table commerce_bank_line_documents enable row level security;
