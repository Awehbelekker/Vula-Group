-- 196: whose money a proof of payment came from (2026-10-06, Ian: "it went through the DIGG
-- account, just paid by me — it should recognise this by the account paid from on the POP").
-- An FNB notice names the paying account's holder ("MR RICHARD D DOWNING", "AWEH BE LEKKER (PTY)
-- LTD"); some banks also show the account number. Each payer name / account ending is recorded
-- once as the business's money or a person's own, so the next POP from it is recognised without
-- asking (vula/commerce/payers.py). Idempotent. RLS on (service role only).

create table if not exists commerce_payer_accounts (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      text not null,
  name           text,                 -- as the bank prints it
  name_key       text,                 -- normalised for matching
  account_last4  text,
  owner          text not null,        -- business | personal
  person         text,                 -- whose own money, when personal
  created_at     timestamptz not null default now()
);

create unique index if not exists commerce_payer_accounts_key
  on commerce_payer_accounts (tenant_id, coalesce(name_key, ''), coalesce(account_last4, ''));

alter table commerce_payer_accounts enable row level security;
