-- _APPLY_2026-11-18_285_289.sql — paste into the Supabase SQL editor and run once.
-- Everything on master that production is missing, as of 18 Nov 2026:
--   285  owner's document choices survive any save (PR #335) — the trigger isn't in production
--   289  each AI call added to the usage meter in one statement (PR #340, 17 Nov audit)
-- and records 254, 255 and 256, which are already in production (checked 18 Nov: the table, the
-- columns and the per_bill check exist) but were never written to vula_schema_migrations.
-- One transaction: all of it applies, or none of it. Idempotent.
begin;

-- ── 285 ──
-- 285_filed_documents_keep_owner_flags.sql — the owner's choices on a document survive any later save.
--
-- 16 Nov 2026 (DIGG, Belladonna): skip invoice 1091 shows another client's site, so the owner kept it off
-- Rudi's client link (fields._hidden_from_client). A later save of the same document — code that reads a
-- document, changes one field and writes the whole `fields` back (asking which project, joining a second
-- page, a re-read) — wrote back a copy from before the owner's choice, and the invoice came back on the
-- client's download. Whatever the code path, a save that drops one of these keys now keeps it:
--
--   _hidden_from_client   kept off client links         _internal   an internal cost sheet
--   _show_on_client_link  shown although it names another project (false = no longer shown)
--   _note                 the owner's note on the document
--
-- To undo a choice, write the key with a new value (false), never by leaving it out.
-- Idempotent.

create or replace function vula_filed_documents_keep_owner_flags() returns trigger
language plpgsql as $$
declare
    k text;
begin
    if old.fields is null or new.fields is null then
        return new;
    end if;
    foreach k in array array['_hidden_from_client', '_show_on_client_link', '_internal', '_note'] loop
        if (old.fields ? k) and not (new.fields ? k) then
            new.fields := new.fields || jsonb_build_object(k, old.fields -> k);
        end if;
    end loop;
    return new;
end;
$$;

drop trigger if exists vula_filed_documents_keep_owner_flags on vula_filed_documents;
create trigger vula_filed_documents_keep_owner_flags
    before update of fields on vula_filed_documents
    for each row execute function vula_filed_documents_keep_owner_flags();

-- ── 289 ──
-- 289_ai_usage_add.sql — add one AI call to today's usage row in a single statement.
--
-- 17 Nov 2026 audit: vula/integrations/metering.py read today's row, added in Python and wrote the
-- total back, so two calls finishing together overwrote each other's count and cost. Since #320 the
-- AI allowance is worked out from these rows, so a lost call was AI use nobody paid for. One
-- insert … on conflict … do update adds atomically (the unique key already exists, migration 031).
-- Called by the service role only. Idempotent.

create or replace function vula_ai_usage_add(
    p_tenant_id text, p_day date, p_model text,
    p_prompt_tokens bigint, p_completion_tokens bigint, p_cost_usd numeric
) returns void
    language sql security invoker set search_path = public, pg_temp as $$
    insert into vula_ai_usage (tenant_id, day, model, calls, prompt_tokens, completion_tokens, est_cost_usd, updated_at)
    values (p_tenant_id, p_day, p_model, 1, p_prompt_tokens, p_completion_tokens, p_cost_usd, now())
    on conflict (tenant_id, day, model) do update set
        calls             = vula_ai_usage.calls + 1,
        prompt_tokens     = vula_ai_usage.prompt_tokens + excluded.prompt_tokens,
        completion_tokens = vula_ai_usage.completion_tokens + excluded.completion_tokens,
        est_cost_usd      = vula_ai_usage.est_cost_usd + excluded.est_cost_usd,
        updated_at        = now();
$$;

revoke all on function vula_ai_usage_add(text, date, text, bigint, bigint, numeric) from public, anon, authenticated;
grant execute on function vula_ai_usage_add(text, date, text, bigint, bigint, numeric) to service_role;

-- ── record them ──
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values
  ('254_project_final_accounts.sql', 254, '41943b42073e2ba7267b240c3fde03c0f772a9f61368ab38556b1fc6023d81b8', 'already in production, recorded 18 Nov (claude-code 01G4LT7Z)', true),
  ('255_project_fee_basis.sql', 255, 'a53d3e3494e025f0a09a35859f99e7d9ec9c8362836cfe46de1ede88ce215639', 'already in production, recorded 18 Nov (claude-code 01G4LT7Z)', true),
  ('256_project_fee_per_bill.sql', 256, 'c376bdb6a5c9a957bfa301a1177d665927a807244e30c47d7fa9020fb39e2e24', 'already in production, recorded 18 Nov (claude-code 01G4LT7Z)', true),
  ('285_filed_documents_keep_owner_flags.sql', 285, '1ad3af5f980b0649ef84e9bbc6ea2a06527aaee7b6c7b301a1acfdaf1fc8727b', 'Ian via SQL editor (prepared by claude-code 01G4LT7Z)', false),
  ('289_ai_usage_add.sql', 289, '83fda7147659e4d0664f33e93844a87726cafc76314cff769175b44b9292b795', 'Ian via SQL editor (prepared by claude-code 01G4LT7Z)', false)
on conflict do nothing;
commit;

-- Check: should return 254, 255, 256, 285, 289
-- select number from vula_schema_migrations where number in (254,255,256,285,289) order by 1;
