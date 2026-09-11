"""Run the chat benchmark against the real, live-configured model and print a scorecard.

Usage:
    railway run python scripts/benchmark_chat.py --yes                # all scenarios
    railway run python scripts/benchmark_chat.py --yes --category tool_selection
    railway run python scripts/benchmark_chat.py --yes --id ni_delivery_fee_missing_price

Hits the real LLM route (local or cloud, whichever core.llm_router resolves to right now) — not
free, not instant, not deterministic. See scripts/benchmarks/__init__.py for why this is
deliberately separate from the pytest suite (tests/test_known_bad_transcripts.py is the
deterministic, CI-run counterpart).

⚠️  This also runs against Vula's real, live tenant IDs (digg-demo, off-the-hook — see
scenarios.py's own docstring for why there's no separate fixture tenant). A confirm_flow
scenario genuinely calls the real skill's real tools: cf_create_invoice_two_turns, for one,
can create a REAL row in commerce_invoices and — depending on what the skill does on
confirmation — potentially send a REAL WhatsApp message to the real customer phone number in
its metadata. This is not a sandbox. --yes is required (2026-09-11, pre-go-live brief item #3)
precisely so nobody runs this by muscle memory and finds out afterward.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from scripts.benchmarks.runner import run_all, scorecard
from scripts.benchmarks.scenarios import ALL_SCENARIOS

_WARNING = """
⚠️  This hits REAL Vula tenants (digg-demo, off-the-hook) with REAL skill runs — not a
    sandbox. A confirm_flow scenario (e.g. cf_create_invoice_two_turns) can create a REAL
    invoice and may send a REAL WhatsApp message to the real customer phone number in its
    metadata. Pass --yes once you understand and accept that.
""".strip("\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", help="Only run scenarios in this category.")
    parser.add_argument("--id", help="Only run this one scenario id.")
    parser.add_argument("--yes", action="store_true",
                        help="Required: confirms you understand this hits real tenant data.")
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

    if not args.yes:
        print(_WARNING)
        print(f"\n{len(scenarios)} scenario(s) would run: "
              + ", ".join(s.id for s in scenarios))
        print("\nRe-run with --yes to actually execute them.")
        sys.exit(1)

    print(f"Running {len(scenarios)} scenario(s)...\n")
    results = await run_all(scenarios)
    print(scorecard(results))


if __name__ == "__main__":
    asyncio.run(main())
