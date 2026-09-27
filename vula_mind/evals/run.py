"""CLI for the eval harness — see evals/harness.py for what each layer measures.

    python -m evals.run routing
    python -m evals.run tools --model openrouter/anthropic/claude-haiku-4.5 [--skill commerce_admin]
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Vula eval harness")
    sub = ap.add_subparsers(dest="layer", required=True)
    sub.add_parser("routing")
    t = sub.add_parser("tools")
    t.add_argument("--model", required=True)
    t.add_argument("--skill", choices=["email_admin", "commerce_admin", "commerce_assistant"])
    a = ap.parse_args()

    if a.layer == "routing":
        report = harness.run_routing()
    else:
        from core.llm_router import install_ollama_auth
        install_ollama_auth()
        report = asyncio.run(harness.run_tools(a.model, a.skill))
        out = Path(__file__).parent / "reports"
        out.mkdir(exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{a.model.replace('/', '_')}.json"
        (out / name).write_text(json.dumps(report, indent=2, default=str))
        print(f"report: evals/reports/{name}")
    _print(report)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
