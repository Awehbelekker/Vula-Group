"""
scripts/seed_oth_knowledge.py — Build Off the Hook's AI knowledge base.

Ingests into the vula_off_the_hook KB collection so the commerce assistant
can answer real customer questions on WhatsApp:
  - "What's fresh this week?"          → catch-of-the-day, species
  - "Do you deliver to Table View?"    → delivery areas + schedule
  - "Is the chicken hormone-free?"     → brand/sourcing facts
  - "How much is hake?"                → live product catalogue
  - "How do I pay?"                    → ordering + payment FAQ

Sources:
  1. scripts/oth_kb/off_the_hook_brand.txt  — brand + operations
  2. Live product catalogue from the commerce API (37 products)
  3. Generated FAQ block

Run:
    cd vula_mind
    MODEL_EMBED=text-embedding-3-small python -m scripts.seed_oth_knowledge
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TENANT = "off-the-hook"
API = "https://vula-group-production.up.railway.app"


FAQ = """OFF THE HOOK — CUSTOMER FAQ

ORDERING
- Order on WhatsApp: 073 781 5979. Just message what you'd like.
- The fresh fish range rotates weekly — ask "what's fresh this week?" for the current catch.
- Orders are confirmed once payment is received.

DELIVERY
- Off the Hook delivers across Cape Town.
- Delivery days and cut-off times are confirmed when you order.
- Fresh fish is delivered cold to keep it at its best.

PAYMENT
- Pay securely by card via the Yoco payment link sent with your order.
- EFT and other options can be arranged on WhatsApp.

QUALITY & SOURCING
- Fish is sourced directly from Cape Town fishing communities — full traceability.
- Chicken is pasture-raised, GMO-free, hormone-free, antibiotic-free, no brine.
- If anything isn't right with your order, message us and we'll sort it out.

PRODUCTS
- Fresh fish (per kg, weekly rotation), pasture-raised chicken (fresh & frozen),
  frozen seafood (fixed packs), and extras like bone broth and collagen.
"""


async def main():
    from vula.ingestion.pipeline import VulaIngestionPipeline
    import httpx

    pipeline = VulaIngestionPipeline(tenant_id=TENANT)
    total_chunks = 0

    # 1. Brand + operations file
    brand_path = Path(__file__).parent / "oth_kb" / "off_the_hook_brand.txt"
    if brand_path.exists():
        text = brand_path.read_text(encoding="utf-8")
        r = await pipeline.ingest_text(content=text, filename="oth_brand.txt", doc_id="oth_brand")
        print(f"  Brand file: {r.chunks_stored} chunks")
        total_chunks += r.chunks_stored

    # 2. Live product catalogue → structured text
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{API}/v1/commerce/{TENANT}/products")
        products = resp.json().get("products", []) if resp.is_success else []

    if products:
        by_cat: dict[str, list] = {}
        for p in products:
            by_cat.setdefault(p.get("category", "other"), []).append(p)
        lines = ["OFF THE HOOK — CURRENT PRODUCT CATALOGUE\n"]
        cat_labels = {
            "fresh_fish": "Fresh Fish (per kg, weekly rotation)",
            "fresh_chicken": "Fresh Pasture-Raised Chicken",
            "frozen_chicken": "Frozen Chicken",
            "frozen_seafood": "Frozen Seafood (fixed packs)",
            "extras": "Extras",
        }
        for cat, items in by_cat.items():
            lines.append(f"\n## {cat_labels.get(cat, cat.replace('_',' ').title())}")
            for p in items:
                price = p.get("price_cents", 0) / 100
                stock = "in stock" if p.get("in_stock", True) else "currently unavailable"
                desc = (p.get("description") or "").strip()
                lines.append(f"- {p.get('name')}: R{price:.0f} ({stock}){' — ' + desc if desc else ''}")
        catalogue = "\n".join(lines)
        r = await pipeline.ingest_text(content=catalogue, filename="oth_catalogue.txt", doc_id="oth_catalogue")
        print(f"  Catalogue ({len(products)} products): {r.chunks_stored} chunks")
        total_chunks += r.chunks_stored

    # 3. FAQ
    r = await pipeline.ingest_text(content=FAQ, filename="oth_faq.txt", doc_id="oth_faq")
    print(f"  FAQ: {r.chunks_stored} chunks")
    total_chunks += r.chunks_stored

    print(f"\nOff the Hook KB ready: {total_chunks} chunks in tenant '{TENANT}'")


if __name__ == "__main__":
    asyncio.run(main())
