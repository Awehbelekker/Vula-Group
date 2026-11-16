-- 284_price_vat_basis.sql — whether each learned price includes VAT (16 Nov 2026).
--
-- Ian: "we need to be accurate". vula_price_observations (migration 184) keeps every priced line
-- of a filed invoice, quote or BOQ, and estimates (vula/commerce/estimate.py) cost building work
-- from them — but nothing said whether a line's price was written with VAT in or out, so an
-- estimate could mix the two and be up to 15% off without saying so.
--
-- vat_basis: 'incl' (the lines add up to the total), 'excl' (lines + VAT make the total, or the
-- document says its amounts exclude VAT), 'no_vat' (no VAT on it — e.g. a worker's day rate), or
-- 'unknown'. Set per document by vula/commerce/price_book.py::document_vat_basis; older rows are
-- filled in after boot by price_book.backfill_vat_basis. Idempotent.

alter table vula_price_observations add column if not exists vat_basis text not null default 'unknown';

do $$ begin
    alter table vula_price_observations add constraint vula_price_observations_vat_basis_chk
        check (vat_basis in ('incl', 'excl', 'no_vat', 'unknown'));
exception when duplicate_object then null;
end $$;

-- a worker's own day/week rate (doc_id 'worker:<id>') carries no VAT
update vula_price_observations set vat_basis = 'no_vat'
 where vat_basis = 'unknown' and doc_id like 'worker:%';
