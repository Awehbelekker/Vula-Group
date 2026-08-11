"""
scripts/ingest_website_kb.py — scrape a set of public web pages into a tenant's KB.

Closes a real gap: vula/skills/web_scraper.py's WebFetcher can fetch a page, and
vula/ingestion/pipeline.py's VulaIngestionPipeline can embed+store text, but nothing
glued the two together — there was no "scrape this URL into my knowledge base" path.

There's no whole-site crawler in this codebase (WebFetcher/VulaWebScraper only fetch URLs
you already know) — pass the exact pages worth ingesting rather than a bare domain.

Usage:
    python scripts/ingest_website_kb.py <tenant_id> <url> [<url> ...]
    python scripts/ingest_website_kb.py gerflor https://www.gerflor.co.za/ \
        https://www.gerflor.co.za/professional/products.html
"""
from __future__ import annotations

import asyncio
import sys

# A small delay between requests — the underlying fetcher has no politeness delay of its own,
# and there's no reason to hammer a real company's production site for a one-off KB seed.
_DELAY_SECONDS = 2.0


async def ingest_website(tenant_id: str, urls: list[str]) -> list[dict]:
    from vula.skills.web_scraper import WebFetcher
    from vula.ingestion.pipeline import VulaIngestionPipeline

    fetcher = WebFetcher()
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    results = []
    for i, url in enumerate(urls):
        if i > 0:
            await asyncio.sleep(_DELAY_SECONDS)
        try:
            title, markdown = await fetcher.fetch_markdown(url)
        except Exception as exc:
            results.append({"url": url, "status": "fetch_failed", "error": str(exc)})
            continue
        if not (markdown or "").strip():
            results.append({"url": url, "status": "empty"})
            continue
        content = f"# {title}\n\n{markdown}" if title else markdown
        outcome = await pipeline.ingest_text(content, filename=url, source_type="website")
        results.append({
            "url": url, "title": title, "status": outcome.status,
            "chunks_stored": outcome.chunks_stored, "error": outcome.error,
        })
    return results


async def _main() -> None:
    # Windows consoles default to cp1252 — force UTF-8 so the checkmarks below print cleanly.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    tenant_id = sys.argv[1]
    urls = sys.argv[2:]
    print(f"Ingesting {len(urls)} page(s) into '{tenant_id}'s KB...\n")
    results = await ingest_website(tenant_id, urls)
    ok = 0
    for r in results:
        if r["status"] == "success":
            ok += 1
            print(f"  ✓ {r['url']} → {r['chunks_stored']} chunks")
        else:
            print(f"  ✗ {r['url']} — {r['status']}: {r.get('error') or ''}")
    print(f"\n{ok}/{len(urls)} pages ingested.")


if __name__ == "__main__":
    asyncio.run(_main())
