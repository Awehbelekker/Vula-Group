"""CLI for the eval harness — see evals/harness.py for what each layer measures.

    python -m evals.run routing
    python -m evals.run tools --model openrouter/anthropic/claude-haiku-4.5 [--skill commerce_admin]
    python -m evals.run retrieval                       # offline fixture subset (what CI runs)
    python -m evals.run retrieval --live [--tenant gerflor] [--cases evals/reports/x.yaml]
    python -m evals.run retrieval-synth --tenant gerflor [--per-doc 5]   # unreviewed cases
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from evals import harness


def _print(report: dict) -> None:
    for r in report["rows"]:
        mark = "PASS" if r["ok"] else "FAIL"
        extra = r.get("error") or f"got={r.get('got')} expect={r.get('expect')}"
        secs = f" {r['secs']}s" if "secs" in r else ""
        print(f"{mark}  {r['prompt'][:70]:70}  {extra}{secs}")
    head = f"\n{report['layer']}: {report['passed']}/{report['total']} passed"
    if report["layer"] == "tools":
        head += (f"  |  model {report['model']}  |  p50 {report['p50_secs']}s  p95 {report['p95_secs']}s"
                 f"  |  ~${report['cost_per_100_usd']} per 100 turns  |  errors {report['errors']}")
    print(head)


def _print_retrieval(report: dict) -> None:
    for r in report["rows"]:
        mark = "PASS" if r.get("ok") else "FAIL"
        extra = r.get("error") or ", ".join(r.get("docs") or []) or "(nothing retrieved)"
        print(f"{mark}  {r['tenant'][:18]:18}  {r['question'][:56]:56}  {extra[:90]}")
    print(f"\nretrieval ({report['mode']}): {report['passed']}/{report['total']} passed")
    for name, group in (("all", {"": report["summary"]}), ("tenant", report["by_tenant"]),
                        ("type", report["by_type"])):
        for k, m in group.items():
            print(f"  {name:6} {k:20} n={m['cases']:<3} r@5={m['recall@5']} r@20={m['recall@20']} "
                  f"mrr={m['mrr']} ndcg@10={m['ndcg@10']} in_prompt={m['in_prompt']} "
                  f"fact_in_prompt={m['fact_in_prompt']} abstain={m['abstention']} "
                  f"leak={m['leak_rate']} p50={m['p50_ms']}ms p95={m['p95_ms']}ms "
                  f"ctx_tok={m['context_tokens']} embeds={m['embed_calls']}")


def _save(report: dict, label: str) -> str:
    out = Path(__file__).parent / "reports"
    out.mkdir(exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{label.replace('/', '_')}.json"
    (out / name).write_text(json.dumps(report, indent=2, default=str))
    return f"evals/reports/{name}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Vula eval harness")
    sub = ap.add_subparsers(dest="layer", required=True)
    sub.add_parser("routing")
    t = sub.add_parser("tools")
    t.add_argument("--model", required=True)
    t.add_argument("--skill", choices=["email_admin", "commerce_admin", "commerce_assistant"])
    r = sub.add_parser("retrieval")
    r.add_argument("--live", action="store_true", help="real tenants, read-only (needs the keys)")
    r.add_argument("--tenant")
    r.add_argument("--cases", help="a YAML file of cases (e.g. reviewed synthetic cases)")
    s = sub.add_parser("retrieval-synth")
    s.add_argument("--tenant", required=True)
    s.add_argument("--per-doc", type=int, default=5)
    a = ap.parse_args()

    if a.layer in ("retrieval", "retrieval-synth"):
        import yaml
        from evals import retrieval
        if a.layer == "retrieval-synth":
            from core.llm_router import install_ollama_auth
            install_ollama_auth()
            cases = asyncio.run(retrieval.synthesize(a.tenant, per_doc=a.per_doc))
            out = Path(__file__).parent / "reports"
            out.mkdir(exist_ok=True)
            path = out / f"retrieval_synth_{a.tenant}_{time.strftime('%Y%m%d-%H%M%S')}.yaml"
            path.write_text(yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True))
            print(f"{len(cases)} UNREVIEWED cases: {path} — check a sample by hand, then "
                  f"python -m evals.run retrieval --live --cases {path}")
            return 0
        cases = retrieval.load_cases(Path(a.cases)) if a.cases else None
        if a.live:
            from core.llm_router import install_ollama_auth
            install_ollama_auth()
            report = asyncio.run(retrieval.run_live(cases, tenant=a.tenant))
        else:
            report = asyncio.run(retrieval.run_fixture(cases))
        print(f"report: {_save(report, 'retrieval-' + report['mode'])}")
        _print_retrieval(report)
        return 0

    if a.layer == "routing":
        report = harness.run_routing()
    else:
        from core.llm_router import install_ollama_auth
        install_ollama_auth()
        report = asyncio.run(harness.run_tools(a.model, a.skill))
        print(f"report: {_save(report, a.model)}")
    _print(report)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
