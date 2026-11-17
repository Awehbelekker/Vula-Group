"""Read a supplier's page and show which prices Vula would keep — writes nothing.

    python tools/rates_check.py https://www.cashbuild.co.za/529-brickwork
    python tools/rates_check.py --file saved_page.html --name Cashbuild

For each price the model reads off the page it prints KEPT, or DROPPED with the reason:
not_on_page (the figure isn't printed on the page) or out_of_band (it can't be the price of that
thing, e.g. a R0.50 brick). Use it before adding a source to ConstructionRatesScraper.SOURCES
(trade costing, 14 Nov 2026).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _text_of(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="page to read")
    ap.add_argument("--file", help="a saved copy of the page instead of fetching it")
    ap.add_argument("--name", default="", help="supplier name to show")
    ap.add_argument("--prompt", default="Extract construction material prices in ZAR.")
    args = ap.parse_args()
    if not args.url and not args.file:
        ap.error("give a URL or --file")

    from vula.takeoff import market_checks
    from vula.takeoff.construction_rates_scraper import RateExtractor, _parse_json_array, _rates_from_items
    ex = RateExtractor()
    content = _text_of(Path(args.file).read_text(errors="replace")) if args.file else await ex.fetcher.fetch(args.url)
    name = args.name or (args.url or args.file)
    page = content[:8000]
    print(f"{name}: {len(content)} characters read ({len(page)} sent to the model)")

    # Show every row the model read, kept or not (the scraper itself only returns kept ones).
    import vula.research as research
    seen = {}
    real = research._complete

    async def capture(task, messages, **kw):
        text = await real(task, messages, **kw)
        seen["raw"] = text
        return text
    research._complete = capture
    try:
        cat = await ex._extract_one(args.url or "", name, args.prompt, content)
    finally:
        research._complete = real
    items = _parse_json_array(seen.get("raw") or "") or []
    rows = _rates_from_items(items, name)
    for r in rows:
        ok, reason, family = market_checks.check(r.label, r.unit, r.low, r.high, page)
        price = f"R{r.low:,.2f}" + (f"–R{r.high:,.2f}" if r.high != r.low else "")
        print(f"  {'KEPT   ' if ok else 'DROPPED'} {price:>22} per {r.unit:<6} {r.label[:60]}"
              f"  [{family or 'no band'}]{'' if ok else '  ← ' + reason}")
    print(f"status: {cat.status}{' — ' + cat.error if cat.error else ''}; kept {len(cat.rates)} of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
