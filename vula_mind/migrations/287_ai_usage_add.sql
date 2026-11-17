-- 287_ai_usage_add.sql — add one AI call to today's usage row in a single statement.
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
