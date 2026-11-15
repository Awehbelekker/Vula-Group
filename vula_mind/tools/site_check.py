"""
tools/site_check.py — how well "Import from your website" reads a real site, without saving anything.

12 Nov 2026 (Ian: "check how strong our website scrape works"). Runs the website import's reading
(vula/commerce/site_import.py::inspect_site) on a site and prints what it found: how the pages
were found, products with prices, photos and descriptions, terms / privacy / returns / shipping,
phone / email / address / hours / socials, linked PDFs, and anything it couldn't read. Nothing is
written: no products, knowledge base, drafts, photos or status.

Run from vula_mind/, on a machine that can reach the site:

    python tools/site_check.py https://seedleme.com
    python tools/site_check.py seedleme.com --json        # the raw result
    python tools/site_check.py a.co.za b.co.za c.co.za     # several, one summary line each at the end

What a miss looks like here is what to add a test fixture for (tests/test_site_facts.py).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tick(ok) -> str:
    return "yes" if ok else "NO"


def report(st: dict) -> str:
    cov = st.get("coverage") or {}
    pr, pol, con, pg = (cov.get("products") or {}), (cov.get("policies") or {}), \
        (cov.get("contact") or {}), (cov.get("pages") or {})
    lines = [f"\n== {st.get('url')} — {st.get('step')}" + (f": {st['error']}" if st.get("error") else "")]
    lines.append(f"Pages: {pg.get('found', 0)} found by {pg.get('how') or '?'}, {pg.get('read', 0)} read, "
                 f"{pg.get('used', 0)} with enough text, {pg.get('skipped', 0)} too short")
    lines.append(f"Products ({st.get('platform') or 'no shop feed'}): {pr.get('found', 0)} found, "
                 f"{pr.get('with_price', 0)} priced, {pr.get('with_image', 0)} with a photo, "
                 f"{pr.get('with_description', 0)} with a description")
    lines.append("Policies: " + ", ".join(f"{k} {_tick(v)}" for k, v in pol.items()))
    for k, v in pol.items():
        if v:
            lines.append(f"  {k}: {v}")
    lines.append("Contact: " + ", ".join(f"{k} {_tick(v)}" for k, v in con.items()))
    for k, v in (st.get("facts") or {}).items():
        lines.append(f"  {k}: {v}")
    if cov.get("pdfs"):
        lines.append("PDFs worth reading: " + "; ".join(cov["pdfs"]))
    if st.get("note"):
        lines.append(f"Note: {st['note']}")
    if st.get("catalogue_note"):
        lines.append(f"Catalogue: {st['catalogue_note']}")
    for e in (st.get("errors") or [])[:8]:
        lines.append(f"  could not read: {e}")
    return "\n".join(lines)


def summary_line(st: dict) -> str:
    cov = st.get("coverage") or {}
    pol = cov.get("policies") or {}
    con = cov.get("contact") or {}
    pr = cov.get("products") or {}
    return (f"{st.get('url')}: {(cov.get('pages') or {}).get('used', 0)} pages, "
            f"{pr.get('with_price', 0)}/{pr.get('found', 0)} products priced, "
            f"policies {sum(1 for v in pol.values() if v)}/{len(pol) or 4}, "
            f"contact {sum(1 for v in con.values() if v)}/{len(con) or 5}")


async def main(urls, as_json: bool, max_pages: int) -> int:
    from vula.commerce.site_import import inspect_site
    results = []
    for url in urls:
        try:
            st = await inspect_site(url, max_pages=max_pages)
        except ValueError as exc:
            print(f"{url}: {exc}")
            continue
        results.append(st)
        print(json.dumps(st, indent=2, default=str) if as_json else report(st))
    if len(results) > 1 and not as_json:
        print("\n== Summary")
        for st in results:
            print(summary_line(st))
    return 0 if results else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Measure the website import on real sites (writes nothing).")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--json", action="store_true", help="print the raw result")
    ap.add_argument("--max-pages", type=int, default=60)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.urls, a.json, a.max_pages)))
