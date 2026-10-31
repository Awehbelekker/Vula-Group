# Measuring model calls, and the Jev decision benchmark

_31 Oct 2026._ Every model call Vula makes through litellm is now measured. Jev (TypeSafe's
decision model on OpenRouter) can be benchmarked against today's classifiers from the master panel.
No prompt or reply text is ever stored; only labels and numbers are.

## What is measured

`vula/integrations/metering.py` already recorded tokens and estimated cost per business per day
(`vula_ai_usage`). Its litellm callback now also writes one row per call to
`vula_reasoning_telemetry`, with `system = 'vula-llm-call'`. Each row holds:

- **`task`:** the label `core/llm_router.resolve_generation_route()` was called with. Every caller
  now passes one, for example `rag_answer`, `query_expansion`, `escalation_classify` or
  `kb_rerank`.
- **`outcome`:** `ok` or `error`.
- **`tenant_id`:** the business, when known.
- **`extra`:**
  - `latency_ms`
  - `prompt_tokens`
  - `completion_tokens`
  - `est_cost_usd`
  - `model`
  - `backend` (`local` or `cloud`)

### Per task, last 7 days

```sql
select task,
       count(*)                                                        as calls,
       count(*) filter (where outcome = 'error')                       as errors,
       count(*) filter (where extra->>'backend' = 'cloud')             as cloud,
       percentile_cont(0.5)  within group (order by (extra->>'latency_ms')::int) as p50_ms,
       percentile_cont(0.95) within group (order by (extra->>'latency_ms')::int) as p95_ms,
       round(avg((extra->>'prompt_tokens')::int))                      as avg_tokens_in,
       round(sum((extra->>'est_cost_usd')::numeric), 4)                as cost_usd
from vula_reasoning_telemetry
where system = 'vula-llm-call' and created_at > now() - interval '7 days'
group by task order by calls desc;
```

## The decision benchmark

**Master › Benchmark › Decision benchmark** runs `vula/decision_bench.py` on Vula's own server,
which holds the OpenRouter key. It covers three decisions, each with about 200 made-up, labelled
cases in `evals/decisions/`:

| Decision | Today's path |
|---|---|
| `skill` (which knowledge skill answers) | `HRMOrchestrator._llm_classify_skill`: local model, then cloud |
| `escalation` (wants a person, upset), in en, af, zu, xh, st, nso, tn and mixed | `vula/escalation.classify_customer_message` |
| `expense_purpose` (petrol, clients, accommodation, other, uncertain) | `expenses.classify_purpose_category` |

Every case goes to Jev (`core/decision_client.py`) and to today's function.

**Before anything goes to Jev:**

- the text is cut to 600 characters;
- phone numbers, SA ID numbers, bank account numbers and emails are replaced;
- no business is named;
- OpenRouter is asked not to collect or keep the data.

**What is stored:** each answer goes in as one row, `system = 'vula-decision-bench'`. Rows carry
the case id, language, expected and given answer, time, confidence and cost, but not the text. One
`outcome = 'summary'` row closes each run.

**What the summary reports, per decision and side:**

- accuracy, overall and per language;
- p50 and p95 time;
- cost per 1,000 decisions;
- how many answers failed;
- the fallback rate. For Jev this counts answers that failed, were below
  `DECISION_MIN_CONFIDENCE` (0.75), or were slower than `DECISION_TIMEOUT_S` (1 s).

**The verdict** is `flip` only when Jev is within 2 points of today's accuracy and falls back on
fewer than 20% of cases. Languages where Jev trails today by more than 2 points are named.

The non-English cases were machine-written. Have a native speaker check them before trusting
their per-language scores.

## Not yet

Nothing live calls Jev. Shadow mode comes next, and only once the benchmark has been read. In
shadow mode Jev answers next to today's model, but only today's answer is used, for chosen
businesses only.

The Jev endpoint (`/api/alpha/decisions`) is an alpha API whose exact answer format couldn't be
checked from the development session, because openrouter.ai isn't in its allowed network
domains. An answer that doesn't parse is counted as a failure, so the benchmark shows that rather
than hiding it.
