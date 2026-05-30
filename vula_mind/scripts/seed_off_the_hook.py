"""
scripts/seed_off_the_hook.py — Seed Off the Hook as a Vula Commerce tenant.

Run once:
    cd vula_mind
    python scripts/seed_off_the_hook.py

Creates:
  - Off the Hook tenant in the local SQLite tenant DB
  - 12 seafood products in Supabase (commerce_products table)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from vula.models.tenants import get_tenant_db
from supabase import create_client

TENANT_ID = "off-the-hook"
WHATSAPP_NUMBER = "0737815979"  # → normalised to 27737815979

PRODUCTS = [
    # ── Linefish ─────────────────────────────────────────────────────────────
    {
        "name": "Cape Yellowtail",
        "slug": "cape-yellowtail",
        "description": "Line-caught fresh yellowtail from Hout Bay. Firm, flavourful — best on the braai.",
        "category": "linefish",
        "price_cents": 18500,    # R185.00
        "weight_grams": 1000,
        "serves": "2–3 people",
        "catch_source": "Hout Bay",
        "fisherman_name": "Skipper Nico",
        "is_daily_catch": True,
    },
    {
        "name": "Snoek — Whole",
        "slug": "snoek-whole",
        "description": "Cape Town's favourite. Fresh whole snoek, cleaned and ready to braai or pickle.",
        "category": "linefish",
        "price_cents": 16000,    # R160.00
        "weight_grams": 1500,
        "serves": "3–4 people",
        "catch_source": "Kalk Bay",
        "is_daily_catch": True,
    },
    {
        "name": "Kabeljou (Kob)",
        "slug": "kabeljou-kob",
        "description": "Premium local kob fillets. White, flaky, and versatile — pan-fry, bake, or steam.",
        "category": "linefish",
        "price_cents": 22000,    # R220.00
        "weight_grams": 800,
        "serves": "2 people",
        "catch_source": "Hout Bay",
        "fisherman_name": "Skipper Danie",
        "is_daily_catch": False,
    },
    {
        "name": "Red Roman",
        "slug": "red-roman",
        "description": "Beautiful Cape red roman — sweet, delicate flesh. A Western Cape classic.",
        "category": "linefish",
        "price_cents": 19500,    # R195.00
        "weight_grams": 700,
        "serves": "2 people",
        "catch_source": "Kalk Bay",
        "is_daily_catch": True,
    },
    # ── Shellfish ─────────────────────────────────────────────────────────────
    {
        "name": "West Coast Rock Lobster",
        "slug": "west-coast-rock-lobster",
        "description": "Wild-caught West Coast crayfish tails. Legally harvested, permit-certified. Special occasion worthy.",
        "category": "crayfish",
        "price_cents": 42000,    # R420.00
        "weight_grams": 600,
        "serves": "2 people",
        "catch_source": "West Coast",
        "is_daily_catch": False,
    },
    {
        "name": "Prawns — Tiger (1kg)",
        "slug": "tiger-prawns-1kg",
        "description": "Jumbo tiger prawns, head-on. Perfect on the braai or in a Cape Malay curry.",
        "category": "shellfish",
        "price_cents": 28000,    # R280.00
        "weight_grams": 1000,
        "serves": "2–3 people",
        "catch_source": "Mozambique",
        "is_daily_catch": False,
    },
    {
        "name": "Calamari Tubes & Tentacles (500g)",
        "slug": "calamari-500g",
        "description": "Fresh cleaned calamari, tubes and tentacles. Flash-fry or slow braise.",
        "category": "shellfish",
        "price_cents": 12000,    # R120.00
        "weight_grams": 500,
        "serves": "2 people",
        "catch_source": "Cape waters",
        "is_daily_catch": True,
    },
    {
        "name": "Mussels — Black (1kg)",
        "slug": "black-mussels-1kg",
        "description": "Western Cape farmed mussels. Cleaned and ready for your white wine pot.",
        "category": "shellfish",
        "price_cents": 8500,     # R85.00
        "weight_grams": 1000,
        "serves": "2 people",
        "catch_source": "Saldanha Bay",
        "is_daily_catch": False,
    },
    # ── Smoked ───────────────────────────────────────────────────────────────
    {
        "name": "Smoked Snoek Pâté (250g)",
        "slug": "smoked-snoek-pate",
        "description": "House-smoked snoek, flaked and blended with lemon cream. Ready to serve.",
        "category": "smoked",
        "price_cents": 9500,     # R95.00
        "weight_grams": 250,
        "serves": "6–8 as starter",
        "catch_source": "Kalk Bay",
        "is_daily_catch": False,
    },
    {
        "name": "Hot-Smoked Yellowtail (500g)",
        "slug": "hot-smoked-yellowtail",
        "description": "Whole sides of hot-smoked yellowtail. Eat as is or flake into pasta.",
        "category": "smoked",
        "price_cents": 16500,    # R165.00
        "weight_grams": 500,
        "serves": "2–3 people",
        "catch_source": "Hout Bay",
        "is_daily_catch": False,
    },
    # ── Box Deals ────────────────────────────────────────────────────────────
    {
        "name": "Braai Box — Family (serves 6)",
        "slug": "braai-box-family",
        "description": "The ultimate Cape braai box: whole snoek, yellowtail fillets, tiger prawns & calamari. Everything you need.",
        "category": "box_deal",
        "price_cents": 62000,    # R620.00
        "weight_grams": 4000,
        "serves": "5–6 people",
        "catch_source": "Hout Bay & Kalk Bay",
        "is_daily_catch": False,
    },
    {
        "name": "Weekly Catch Box",
        "slug": "weekly-catch-box",
        "description": "Our freshest pick of the week — 2 linefish portions, 500g shellfish, ready to cook. Subscribe & save.",
        "category": "box_deal",
        "price_cents": 38000,    # R380.00
        "weight_grams": 2000,
        "serves": "3–4 people",
        "catch_source": "Cape Town waters",
        "is_daily_catch": True,
    },
]


def seed_tenant():
    db = get_tenant_db()
    db.upsert(
        tenant_id=TENANT_ID,
        company_name="Off the Hook",
        whatsapp=WHATSAPP_NUMBER,
        email="orders@offthehook.co.za",
        plan="growth",
    )
    db.add_phone(TENANT_ID, WHATSAPP_NUMBER, label="orders", role="admin")
    print(f"✓ Tenant '{TENANT_ID}' registered with WhatsApp {WHATSAPP_NUMBER}")


def seed_products():
    import uuid
    from datetime import datetime, timezone

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    now = datetime.now(timezone.utc).isoformat()

    rows = [
        {
            "id": str(uuid.uuid4()),
            "tenant_id": TENANT_ID,
            "created_at": now,
            "updated_at": now,
            "in_stock": True,
            **p,
        }
        for p in PRODUCTS
    ]

    # Upsert by (tenant_id, slug)
    result = client.table("commerce_products").upsert(rows, on_conflict="tenant_id,slug").execute()
    print(f"✓ {len(result.data)} products seeded for '{TENANT_ID}'")


if __name__ == "__main__":
    seed_tenant()
    seed_products()
    print("\nOff the Hook is ready. Run migration 002_vula_commerce.sql in Supabase first if you haven't.")
