-- 253: a business is not VAT-registered until it says so; the general ledger is append-only.
-- 9 Nov 2026 go-live audit (money, medium). Idempotent.
--
-- 1. commerce_invoice_settings.vat_registered defaulted to TRUE (migration 032), so a business
--    that saved its first invoice settings for any reason (a logo, a bank account) silently became
--    VAT-registered and its invoices added 15%. Charging VAT without being registered is the worse
--    mistake, so the default is now false. Existing rows keep their value: every live business has
--    it set explicitly (checked 9 Nov 2026). The code reads a missing row as not registered too.
alter table commerce_invoice_settings alter column vat_registered set default false;

-- 2. journal_entries / journal_lines had no protection (only the tap-to-pay ledger did, 199/205/
--    232): a posted entry could be edited or deleted, and deleting an entry took its lines with it.
--    The app never changes or removes either (vula/commerce/ledger.py only inserts, through
--    post_journal_entry); a correction is a new reversing entry. For deliberate operator work in the
--    SQL editor (e.g. removing a test business), run `set local vula.ledger_maintenance = 'on';`
--    in the same transaction first.
create or replace function vula_ledger_append_only() returns trigger
    language plpgsql set search_path = public, pg_temp as $$
begin
    if coalesce(current_setting('vula.ledger_maintenance', true), '') = 'on' then
        return case when tg_op = 'DELETE' then old else new end;
    end if;
    raise exception '% is append-only: post a reversing entry instead of changing or deleting one', tg_table_name;
end $$;

drop trigger if exists trg_journal_entries_append_only on journal_entries;
create trigger trg_journal_entries_append_only before update or delete on journal_entries
    for each row execute function vula_ledger_append_only();

drop trigger if exists trg_journal_lines_append_only on journal_lines;
create trigger trg_journal_lines_append_only before update or delete on journal_lines
    for each row execute function vula_ledger_append_only();
