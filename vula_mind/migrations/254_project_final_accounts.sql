-- 254_project_final_accounts.sql — a project's agreed final account, shown on the client's link.
--
-- 10 Nov 2026 (Ian, DIGG / Belladonna): the open-book page worked the balance out as the business's
-- cash cost through the bank, plus a percentage fee, less what the client paid — and showed the
-- client "Balance due −R57,917.27" (the business owing him) while the final account the business
-- agreed with him says R32,839.40 the other way. A final account is a list of agreed charges (P&G at
-- a fixed weekly rate, scope of works at agreed amounts), not cost plus a percentage, so no fee
-- percentage turns one into the other. When a project has one, the client's open-book page shows it.
--
--   charged_cents   the final account's total of costs and fee (as agreed, incl. VAT where charged)
--   adjustments     corrections after it was issued: [{"label": "...", "cents": 500000}, ...]
--   as_of           the final account's date
--   note            where it came from (the document it was typed from)
--
-- What the client has paid is not stored here: the page takes it from the bank, as now, so a payment
-- that comes in later lowers the balance on its own. One per project. All money integer cents.
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_project_final_accounts (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    project        text not null,
    as_of          date not null,
    charged_cents  bigint not null check (charged_cents >= 0),
    adjustments    jsonb not null default '[]'::jsonb,
    note           text,
    created_by     text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (tenant_id, project)
);
create index if not exists idx_project_final_accounts_tenant on vula_project_final_accounts (tenant_id);
alter table vula_project_final_accounts enable row level security;
