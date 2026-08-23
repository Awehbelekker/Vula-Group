"""Run the chat benchmark against the real, live-configured model and print a scorecard.

Usage:
    railway run python scripts/benchmark_chat.py                # all scenarios
    railway run python scripts/benchmark_chat.py --category tool_selection
    railway run python scripts/benchmark_chat.py --id ni_delivery_fee_missing_price

Hits the real LLM route (local or cloud, whichever core.llm_router resolves to right now) — not
free, not instant, not deterministic. See scripts/benchmarks/__init__.py for why this is
deliberately separate from the pytest suite (tests/test_known_bad_transcripts.py is the
deterministic, CI-run counterpart).
"""
from __future__ import annotations

import argparse
import asyncio

from scripts.benchmarks.runner import run_all, scorecard
from scripts.benchmarks.scenarios import ALL_SCENARIOS


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", help="Only run scenarios in this category.")
    parser.add_argument("--id", help="Only run this one scenario id.")
    args = parser.parse_args()

    scenarios = ALL_SCENARIOS
    if args.id:
        scenarios = [s for s in scenarios if s.id == args.id]
        if not scenarios:
            print(f"No scenario with id '{args.id}'.")
            return
    elif args.category:
        scenarios = [s for s in scenarios if s.category == args.category]
        if not scenarios:
            print(f"No scenarios in category '{args.category}'.")
            return

    print(f"Running {len(scenarios)} scenario(s)...\n")
    results = await run_all(scenarios)
    print(scorecard(results))


if __name__ == "__main__":
    asyncio.run(main())
