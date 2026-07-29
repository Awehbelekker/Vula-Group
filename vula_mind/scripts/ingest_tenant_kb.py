"""
scripts/ingest_tenant_kb.py — Ingest a tenant's knowledge base into Qdrant.

Builds the per-tenant Qdrant collection (vula_<tenant_id>) that the
commerce_assistant and reasoning skills query for grounded answers. Point it
at a file or a directory of business documents (delivery policy, FAQs, catalog
notes, returns policy, etc.), or run with no path to seed a small built-in KB
for known tenants like Off the Hook.

Usage:
    cd vula_mind
    python scripts/ingest_tenant_kb.py off-the-hook ./kb/off_the_hook
    python scripts/ingest_tenant_kb.py off-the-hook ./kb/delivery_policy.pdf
    python scripts/ingest_tenant_kb.py off-the-hook        # seed built-in KB

Requires Ollama (for bge-m3 embeddings) or OPENROUTER_API_KEY, and a running
Qdrant (local or Qdrant Cloud via QDRANT_API_KEY).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vula.ingestion.pipeline import VulaIngestionPipeline  # noqa: E402

# ── Built-in seed knowledge bases (used when no path is supplied) ──────────────
# Plain-text business context that grounds the assistant's answers. Extend per
# tenant as policies firm up; richer/longer docs should be ingested as files.

_BUILTIN_KB: dict[str, dict[str, str]] = {
    "off-the-hook": {
        "delivery_policy.md": (
            "# Off the Hook — Delivery\n\n"
            "We deliver fresh daily catch door-to-door across Cape Town.\n"
            "Standard delivery is R80. Free delivery on orders over R500.\n"
            "Delivery slots: morning (08:00–12:00), afternoon (12:00–17:00), "
            "and express (2-hour window, +R50).\n"
            "Orders placed before 10:00 can be delivered the same day, subject to stock.\n"
        ),
        "about.md": (
            "# About Off the Hook\n\n"
            "Off the Hook supplies Cape Town's freshest daily catch: linefish "
            "(yellowtail, snoek, kob, red roman), shellfish and prawns, West Coast "
            "crayfish, box deals, and smoked fish.\n"
            "Fresh fish is sold per kg and rotates weekly with the catch. "
            "Frozen seafood is sold per pack at fixed prices.\n"
            "Contact: 073 781 5979.\n"
        ),
        "faq.md": (
            "# Off the Hook — FAQ\n\n"
            "Q: How do I pay? A: We send a secure Yoco payment link at checkout.\n"
            "Q: Can I track my order? A: Yes — reply with your order number "
            "(e.g. OTH-00042) and we'll send an update.\n"
            "Q: Is the fish fresh? A: Yes, linefish is the daily catch and subject "
            "to availability. We'll suggest alternatives if something sells out.\n"
        ),
    },
    # Sourced directly from the live site (awakesa.co.za: homepage, /warranty, /shipping,
    # /support, /compare) 2026-07-29 — no invented figures.
    "awake-sa": {
        "about.md": (
            "# About Awake South Africa\n\n"
            "Awake South Africa is the official South African distributor of Awake electric "
            "watersports boards — RÄVIK jetboards and VINGA eFoils. Based in Cape Town.\n"
            "Contact: info@awakesa.co.za, +27 64 575 5210 (also available on WhatsApp).\n"
        ),
        "products.md": (
            "# Products\n\n"
            "## RÄVIK S — Jetboard\n"
            "Price: R285,000. Top speed: 57 km/h. Ride time: 45 minutes. Weight: 35 kg. "
            "Power: 12 kW. Battery: 2.2 kWh. Construction: carbon fibre.\n"
            "'High-speed rides on the water's surface.'\n\n"
            "## VINGA 2 — eFoil\n"
            "Price: R195,000. Top speed: 40 km/h. Ride time: 90 minutes. Weight: 28 kg. "
            "Power: 5 kW. Battery: 2.2 kWh. Construction: carbon fibre.\n"
            "Lifts above the water for a 'flying' sensation.\n\n"
            "The jetboard offers higher speed and power but shorter ride time; the eFoil "
            "gives longer ride time and a lower price at reduced performance. Both share "
            "identical battery capacity and carbon fibre construction.\n"
            "Accessories are also available. Prices include 15% VAT, shown in ZAR (R).\n"
        ),
        "warranty_policy.md": (
            "# Warranty Policy\n\n"
            "Board & components: 2-year warranty. Battery: 1-year warranty (500 charge cycles).\n"
            "Covers defects in materials/workmanship: motor/drivetrain, electronic control "
            "systems, battery cells within spec, remote control and charging equipment.\n"
            "Not covered: misuse, accidents, neglect; normal wear of footpads/fins/edges; "
            "unauthorized modifications/repairs; improper storage/charging; commercial/rental use.\n"
            "Claim process: 1) contact support with order info, 2) describe the problem with "
            "photos/video if possible, 3) technical assessment within 48 hours, 4) on approval, "
            "repair or replacement is arranged — shipping for warranty claims is covered by "
            "Awake Boards SA.\n"
            "Contact: info@awakesa.co.za, +27 64 575 5210.\n"
        ),
        "shipping_policy.md": (
            "# Shipping Policy\n\n"
            "Standard delivery: free, 5-7 business days, insured, signature required, tracked.\n"
            "Express delivery: R1,500, 2-3 business days, priority handling, insured, "
            "real-time tracking.\n"
            "Delivers across South Africa (Cape Town, Johannesburg, Durban, Pretoria, Port "
            "Elizabeth, Bloemfontein and more) — remote areas may take up to 10 business days.\n"
            "International: ships to Namibia, Botswana, Zimbabwe, Mozambique, and Mauritius — "
            "variable rates, contact for a quote and customs details.\n"
            "All shipments require a signature and ship in custom protective cases; customers "
            "get SMS/email tracking updates.\n"
        ),
        "demo_ride_locations.md": (
            "# Demo Rides\n\n"
            "We encourage every customer to book a demo before purchasing — demo fees are "
            "fully redeemable against a purchase. Professional training is included with every "
            "board purchase, or can be booked separately, at any of these Western Cape "
            "locations:\n"
            "Langebaan, Melkbosstrand, Eden on the Bay, MAC Club (Milnerton), V&A Waterfront, "
            "Clifton Beach, Llandudno Beach.\n"
        ),
        "payment_and_financing.md": (
            "# Payment & Financing\n\n"
            "Accepted: all major credit cards, EFT, and PayFast PayJustNow financing at 0% "
            "interest over 3 payments.\n"
            "A separate online quote tool also offers 12/24/36-month financing plans, priced "
            "as a standard monthly instalment off the board's price.\n"
        ),
        "faq.md": (
            "# Awake South Africa — FAQ\n\n"
            "Q: How long does shipping take? A: Standard delivery within South Africa takes "
            "5-7 business days; express (2-3 days) is available for R1,500.\n"
            "Q: Do you offer training? A: Yes — professional training is included with every "
            "board purchase, or can be booked separately at any demo location.\n"
            "Q: What's the warranty? A: 2 years on the board and components, 1 year (or 500 "
            "charge cycles) on the battery. Claims are assessed within 48 hours.\n"
            "Q: Can I try before I buy? A: Yes — book a demo at any of our 7 Western Cape "
            "locations; the demo fee is fully redeemable against a purchase.\n"
            "Q: Do you ship outside South Africa? A: Yes, to Namibia, Botswana, Zimbabwe, "
            "Mozambique, and Mauritius — contact us for a quote.\n"
            "Q: Can I pay in instalments? A: Yes — PayFast PayJustNow (0% over 3 payments), "
            "or a 12/24/36-month financing plan via our online quote tool.\n"
        ),
    },
}


async def _ingest_path(tenant_id: str, path: Path) -> int:
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    total = 0
    if path.is_dir():
        results = await pipeline.ingest_directory(path)
        for r in results:
            print(f"  {r.status:8} {r.filename:40} chunks={r.chunks_stored}")
            total += r.chunks_stored
    else:
        r = await pipeline.ingest_file(path)
        print(f"  {r.status:8} {r.filename:40} chunks={r.chunks_stored}")
        if r.error:
            print(f"           error: {r.error}")
        total += r.chunks_stored
    return total


async def _ingest_builtin(tenant_id: str) -> int:
    kb = _BUILTIN_KB.get(tenant_id)
    if not kb:
        print(f"No built-in KB for tenant '{tenant_id}'. Supply a file or directory path.")
        print(f"Known built-in tenants: {', '.join(_BUILTIN_KB) or '(none)'}")
        return 0
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    total = 0
    for filename, content in kb.items():
        r = await pipeline.ingest_text(content, filename=filename)
        print(f"  {r.status:8} {filename:40} chunks={r.chunks_stored}")
        if r.error:
            print(f"           error: {r.error}")
        total += r.chunks_stored
    return total


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_tenant_kb.py <tenant_id> [path-to-file-or-dir]")
        sys.exit(1)

    tenant_id = sys.argv[1]
    print(f"\n🌿 Vula KB ingestion — tenant: {tenant_id}")
    print(f"   Collection: vula_{tenant_id.replace('-', '_')}\n")

    if len(sys.argv) >= 3:
        path = Path(sys.argv[2])
        if not path.exists():
            print(f"Path not found: {path}")
            sys.exit(1)
        total = await _ingest_path(tenant_id, path)
    else:
        total = await _ingest_builtin(tenant_id)

    print(f"\n✓ Done — {total} chunks stored for tenant '{tenant_id}'.")


if __name__ == "__main__":
    asyncio.run(main())
