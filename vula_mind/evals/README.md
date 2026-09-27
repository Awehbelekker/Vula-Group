# Vula evals — the test ground

Replayable cases that say what Vula *should* do, scored the same way every time. This is
how a model, prompt, or routing change is decided: on Vula's own work, not on reputation.

| Layer | What it checks | Model? | Where it runs |
|---|---|---|---|
| `routing` | which skill a knowledge-path message reaches (`cases/routing.yaml`) | no | every CI run (`tests/test_evals.py`) |
| `tools` | a skill's **first move** — which tool its real prompt + toolset picks (`cases/tool_choice.yaml`) | yes | by hand, before changing a model |

Tools are never executed — nothing is read from or written to a tenant.

```bash
cd vula_mind
python -m evals.run routing
python -m evals.run tools --model openrouter/anthropic/claude-haiku-4.5
python -m evals.run tools --model ollama_chat/llama3.1:8b --skill commerce_admin   # needs the tunnel
```

`tools` prints pass rate, p50/p95 latency and cost per 100 turns, and saves a JSON report to
`evals/reports/` (git-ignored). Compare models by running the same command with each.

**Adding cases:** every production misroute or wrong first move becomes a case — add it to the
YAML with a `note` saying what went wrong. A routing case that fails blocks CI.
