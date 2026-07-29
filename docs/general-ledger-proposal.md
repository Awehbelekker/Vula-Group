# General ledger — design proposal (not implemented)

**Status: proposal only.** No migration or code has been written for this — per the
security/gap remediation plan, this needs sign-off before any schema lands, since it
touches real financial data for real tenants (OTH, DIGG, awake-sa).

## Problem statement

Confirmed by direct search across all 111 migration files (through `115_rls_shared_infra_tables.sql`):
there is no `journal_entries`/`journal_lines` table, and no `debit_cents`/`credit_cents`
columns, anywhere in the schema. Migration `058_accounting.sql` adds a chart of accounts
(`commerce_accounts`) and learned VAT/category rules (`commerce_txn_rules`), and tags
`account_code`/`vat_cents`/`vat_treatment` onto `commerce_bank_transactions` and
`commerce_expenses` — but those are still **single-entry** records (one row = one
transaction, categorised). Nothing enforces or even represents a debit/credit invariant.
This is fine for the current use case (bank-rec categorisation, VAT reporting, a
plain-language P&L narrative), but it means Vula cannot produce a trial balance, a
balance sheet, or hand an accountant a real general ledger — every one of those needs
double-entry.

## Where money is written today (unaffected by this proposal)

- `commerce_bank_transactions` — written by `vula/commerce/bank_rec.py` (bank statement
  ingestion, upserted per parsed transaction).
- `commerce_expenses` — written from several places: `vula/api/commerce.py` (manual
  expense entry, WhatsApp receipt-scan expense claims), `vula/commerce/expenses.py`,
  `vula/commerce/recurring_bills.py` (recurring bill generation), `vula/commerce/service.py`
  (order-driven cost postings).
- Orders/invoices (`commerce_orders`, `commerce_invoices`) are the revenue side, tracked
  separately again as single-entry status records, not posted anywhere as journal entries.

None of these would change under this proposal — the ledger would be a **new layer that
reads from these events**, not a replacement for how they're captured today.

## Schema sketch (illustrative — not a migration)

```sql
create table journal_entries (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    entry_date   date not null,
    description  text not null,
    source_type  text not null,   -- 'bank_transaction' | 'expense' | 'invoice' | 'manual'
    source_id    uuid,            -- points back at the originating row, when there is one
    created_at   timestamptz not null default now()
);

create table journal_lines (
    id                uuid primary key default gen_random_uuid(),
    journal_entry_id  uuid not null references journal_entries(id) on delete cascade,
    account_id        uuid not null references commerce_accounts(id),
    debit_cents       bigint not null default 0,
    credit_cents      bigint not null default 0,
    memo              text,
    check (debit_cents = 0 or credit_cents = 0),           -- exactly one side per line
    check (debit_cents >= 0 and credit_cents >= 0)
);
```

A per-entry balance invariant (`sum(debit_cents) = sum(credit_cents)` across all lines
sharing a `journal_entry_id`) can't be a plain column CHECK — it needs either a trigger
that runs after all lines for an entry are inserted, or an application-layer guarantee
(the posting function always writes both sides of an entry in one transaction and
verifies the sums before committing). Worth deciding which during implementation, not
guessing here.

`commerce_accounts` (058) becomes the chart-of-accounts FK target directly — no changes
needed there, it's already shaped for this.

## Posting rules — sketch, to be refined with the user

Each existing money-moving event type would map to a debit/credit pair once implemented:

| Event | Debit | Credit |
|---|---|---|
| Order paid | Bank/Cash | Sales (revenue account) |
| Expense recorded | Expense account (per `commerce_expenses.account_code`) | Bank/Cash or Accounts Payable |
| VAT on a sale | (part of the sale amount) | VAT Output (liability) |
| VAT on a purchase | VAT Input (asset) | (part of the expense amount) |
| Refund | Sales (reversal) | Bank/Cash |

This table is a starting sketch based on `commerce_accounts.type` (income/expense/asset/
liability/equity) already existing — the exact mapping needs a pass with whoever owns
the books (Ian, or an accountant) before it's trustworthy enough to post real entries.

## RLS

Would follow the Phase 1 pattern directly: `journal_entries` has `tenant_id` (Tier A,
standard `tenant_isolation` policy). `journal_lines` has no `tenant_id` of its own — Tier
B, join through `journal_entry_id` → `journal_entries.tenant_id`, exactly like
`commerce_order_items`/`commerce_cart_items` in `114_rls_join_scoped_tables.sql`.

## Migration numbering

Not assigned yet — next available at time of writing is `116` (after migrations 112-115
of this same remediation pass), but this should be treated as "next available whenever
this is actually built," not reserved now.

## Open questions for sign-off

1. **Dual-write vs. cutover** — does the ledger start recording NEW transactions only
   (dual-write: existing single-entry tables keep working exactly as they do now, ledger
   entries get posted alongside), or does it also need a historical backfill from
   existing `commerce_bank_transactions`/`commerce_expenses`/paid orders?
2. **Backfill scope**, if wanted — how far back, and is approximate/reconstructed
   historical data acceptable, or does it need to be exact?
3. **Reporting requirements** — what does this actually need to produce (trial balance,
   balance sheet, income statement) and for whom (Ian directly, or handed to each
   tenant's accountant)? This shapes how much of the mapping table above needs to be
   fully correct vs. good-enough for internal use.
4. **Posting timing** — real-time (post a journal entry the moment an order is paid /
   expense is logged) vs. batch (a nightly job that reconciles the period and posts
   entries)? Real-time is simpler to reason about; batch is more forgiving of
   corrections/edits to the source record before it's posted.
