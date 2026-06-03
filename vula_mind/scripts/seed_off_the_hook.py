"""
scripts/seed_off_the_hook.py — Seed Off the Hook as a Vula Commerce tenant.

Off the Hook — "Quality food delivered to your door"
Cell: 073 781 5979 | info@offthehook.capetown

Product range:
  1. Fresh Fish (main focus) — sold per kg, weekly rotation
  2. Fresh Pasture-Raised Chicken — sold per kg, GMO-free, no hormones, no brine
  3. Frozen Chicken — sold per kg
  4. Frozen Seafood — sold per pack at fixed prices
  5. Extras (broths, collagen packs)

Run once after running migration 002_vula_commerce.sql in Supabase:
    cd vula_mind
    python scripts/seed_off_the_hook.py
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from vula.models.tenants import get_tenant_db
from supabase import create_client

TENANT_ID = "off-the-hook"
WHATSAPP_NUMBER = "0737815979"     # Off the Hook orders number
STACY_WHATSAPP  = "0722684085"    # Stacy — owner
ROLAND_WHATSAPP = "0721822828"    # Roland — operations

# ── All products from the Off the Hook price list ──────────────────────────────
#
# price_cents = price per kg (for sold_by='kg') or fixed pack price (for sold_by='pack')
# All prices in ZAR cents (R290/kg = 29000 cents)

PRODUCTS = [

    # ══════════════════════════════════════════════════════════════════════════
    # FRESH FISH — sold per kg, weekly rotation
    # ══════════════════════════════════════════════════════════════════════════
    {
        "name": "Yellowfin Tuna Steaks",
        "slug": "yellowfin-tuna-steaks",
        "description": "Fresh Cape Town yellowfin tuna steaks. Rich, firm, and versatile — sear, braai, or poke bowl. Subject to weekly availability.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 29000,   # R290/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
        "notes": "Subject to availability — ask us on WhatsApp for this week's stock.",
    },
    {
        "name": "Hake Fillets",
        "slug": "hake-fillets",
        "description": "Classic Cape hake fillets — flaky, mild, and perfect for fish braais, baking, or fish and chips.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 16000,   # R160/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
    },
    {
        "name": "Hake Centre Cuts",
        "slug": "hake-centre-cuts",
        "description": "Thick hake centre cuts — more meat, less fuss. Great on the braai or oven-baked.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 22000,   # R220/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
    },
    {
        "name": "Kingklip Fillets",
        "slug": "kingklip-fillets",
        "description": "Premium kingklip fillets — South Africa's most prized table fish. Buttery, firm, and delicious.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 24000,   # R240/kg
        "pack_size": "per kg",
        "is_daily_catch": False,
        "notes": "Subject to availability.",
    },
    {
        "name": "Kingklip Centre Cuts (skinless)",
        "slug": "kingklip-centre-cuts-skinless",
        "description": "Thick skinless kingklip centre cuts — the finest cut of premium kingklip, ready to cook.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 29000,   # R290/kg
        "pack_size": "per kg",
        "is_daily_catch": False,
        "notes": "Subject to availability.",
    },
    {
        "name": "Jacopever Fillets",
        "slug": "jacopever-fillets",
        "description": "Local jacopever (red stumpnose) fillets — an underrated Cape fish, great value and excellent eating.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 7500,    # R75/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
    },
    {
        "name": "Atlantic Mackerel Fillets",
        "slug": "atlantic-mackerel-fillets",
        "description": "Oily, rich Atlantic mackerel fillets — high in omega-3, great smoked, grilled, or pickled.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 8000,    # R80/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
    },
    {
        "name": "Whole Octopus (unclean)",
        "slug": "whole-octopus-unclean",
        "description": "Whole unclean octopus — for the adventurous cook. Slow-braise or braai Cape-style.",
        "category": "fresh_fish",
        "sold_by": "kg",
        "price_cents": 9500,    # R95/kg
        "pack_size": "per kg",
        "is_daily_catch": True,
        "notes": "Sold unclean. Cleaning instructions available on request.",
    },

    # ══════════════════════════════════════════════════════════════════════════
    # FRESH PASTURE-RAISED CHICKEN — sold per kg
    # GMO-free · No growth hormones · No routine antibiotics · No brine
    # ══════════════════════════════════════════════════════════════════════════
    {
        "name": "Whole Chicken (Fresh)",
        "slug": "whole-chicken-fresh",
        "description": "Pasture-raised whole chicken. GMO-free, no growth hormones, no routine antibiotics, no brine. The real thing.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 9500,    # R95/kg
        "pack_size": "per kg",
        "weight_grams": 1800,   # typical whole chicken
        "notes": "Typical weight 1.5–2.2kg. Price charged per kg.",
    },
    {
        "name": "Drumsticks (Fresh) — 6 per pack",
        "slug": "drumsticks-fresh-6pack",
        "description": "Fresh pasture-raised drumsticks, 6 per pack. Ideal for the braai.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 15000,   # R150/kg
        "pack_size": "6 per pack",
        "weight_grams": 900,
    },
    {
        "name": "Thighs (Fresh) — 4 per pack",
        "slug": "thighs-fresh-4pack",
        "description": "Fresh pasture-raised bone-in thighs, 4 per pack. Juicy, flavourful, and versatile.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 15000,   # R150/kg
        "pack_size": "4 per pack",
        "weight_grams": 900,
    },
    {
        "name": "Breast Fillets (Fresh) — 5 per pack",
        "slug": "breast-fillets-fresh-5pack",
        "description": "Fresh pasture-raised breast fillets, 5 per pack (approx 1.2–1.4kg per pack).",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 15000,   # R150/kg
        "pack_size": "5 per pack (~1.2–1.4kg)",
        "weight_grams": 1300,
    },
    {
        "name": "Breast Fillets (Fresh) — 4 per pack",
        "slug": "breast-fillets-fresh-4pack",
        "description": "Fresh pasture-raised breast fillets, 4 per pack (approx 900g–1kg per pack).",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 15000,   # R150/kg
        "pack_size": "4 per pack (~900g–1kg)",
        "weight_grams": 950,
    },
    {
        "name": "Deboned Whole Chicken (Fresh)",
        "slug": "deboned-whole-chicken-fresh",
        "description": "Fresh pasture-raised deboned whole chicken — perfect for stuffing, rolling, or butterflying.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 18500,   # R185/kg
        "pack_size": "per kg",
        "weight_grams": 1500,
    },
    {
        "name": "Deboned Thighs (Fresh) — 1kg",
        "slug": "deboned-thighs-fresh-1kg",
        "description": "Fresh pasture-raised deboned thighs, skin-on or skinless. Rich flavour, easy to cook.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 18500,   # R185/kg
        "pack_size": "1kg",
        "weight_grams": 1000,
        "notes": "Available skin-on or skinless — specify on order.",
    },
    {
        "name": "Livers (Fresh)",
        "slug": "chicken-livers-fresh",
        "description": "Fresh pasture-raised chicken livers. Available in 250g or 500g packs. Pan-fry with garlic and cream.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 8000,    # R80/kg
        "pack_size": "250g or 500g packs",
        "notes": "Specify pack size on order.",
    },
    {
        "name": "Feet (Fresh) — 1kg",
        "slug": "chicken-feet-fresh-1kg",
        "description": "Fresh pasture-raised chicken feet, 1kg pack. Perfect for collagen-rich stock.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 7000,    # R70/kg
        "pack_size": "1kg",
        "weight_grams": 1000,
    },
    {
        "name": "Necks (Fresh) — 1kg",
        "slug": "chicken-necks-fresh-1kg",
        "description": "Fresh pasture-raised chicken necks, 1kg pack. Slow-cook for rich, gelatinous stock.",
        "category": "fresh_chicken",
        "sold_by": "kg",
        "price_cents": 7000,    # R70/kg
        "pack_size": "1kg",
        "weight_grams": 1000,
    },

    # ══════════════════════════════════════════════════════════════════════════
    # FROZEN CHICKEN — sold per kg
    # ══════════════════════════════════════════════════════════════════════════
    {
        "name": "Deboned Whole Chicken (Frozen)",
        "slug": "deboned-whole-chicken-frozen",
        "description": "Frozen pasture-raised deboned whole chicken. Convenient, portion-ready. No brine, no additives.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 18500,
        "pack_size": "per kg",
        "weight_grams": 1500,
    },
    {
        "name": "Deboned Thighs (Frozen) — 1kg",
        "slug": "deboned-thighs-frozen-1kg",
        "description": "Frozen pasture-raised deboned thighs, skin-on or skinless. 1kg pack.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 18500,
        "pack_size": "1kg",
        "weight_grams": 1000,
        "notes": "Available skin-on or skinless — specify on order.",
    },
    {
        "name": "Thighs (Frozen) — 4 per pack",
        "slug": "thighs-frozen-4pack",
        "description": "Frozen pasture-raised bone-in thighs, 4 per pack.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 15000,
        "pack_size": "4 per pack",
        "weight_grams": 900,
    },
    {
        "name": "Wings (Frozen) — 8 per pack",
        "slug": "wings-frozen-8pack",
        "description": "Frozen pasture-raised wings, 8 per pack. Braai, air-fry, or slow-cook.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 15000,
        "pack_size": "8 per pack",
        "weight_grams": 900,
    },
    {
        "name": "Braaipak (Frozen) — 8 pieces",
        "slug": "braaipak-frozen-8pieces",
        "description": "Frozen pasture-raised braaipak, 8 mixed pieces — perfect for a family braai.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 15000,
        "pack_size": "8 pieces per pack",
        "weight_grams": 1200,
    },
    {
        "name": "Spatchcock (Frozen)",
        "slug": "spatchcock-frozen",
        "description": "Frozen pasture-raised spatchcock chicken — butterflied and ready to braai or roast.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 15000,
        "pack_size": "per kg",
        "weight_grams": 1500,
    },
    {
        "name": "Chicken Carcass / Collagen Packs (Frozen) — 1kg",
        "slug": "chicken-carcass-collagen-frozen-1kg",
        "description": "Frozen pasture-raised chicken carcasses for stock and collagen. 1kg pack.",
        "category": "frozen_chicken",
        "sold_by": "kg",
        "price_cents": 7000,
        "pack_size": "1kg",
        "weight_grams": 1000,
    },

    # ══════════════════════════════════════════════════════════════════════════
    # FROZEN SEAFOOD — sold per pack at fixed prices
    # ══════════════════════════════════════════════════════════════════════════
    {
        "name": "Calamari Tubes CLEAN — 1kg",
        "slug": "calamari-tubes-clean-1kg",
        "description": "East Coast Loligo/Chokka calamari tubes, cleaned. 1kg pack. Fry, stuff, or braai.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 16000,   # R160 per pack
        "pack_size": "1kg",
        "weight_grams": 1000,
    },
    {
        "name": "Hake Centre Cuts (Frozen) — 1kg",
        "slug": "hake-centre-cuts-frozen-1kg",
        "description": "Frozen hake centre cuts, 1kg pack. Thick portions, easy to cook from frozen.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 20000,   # R200 per pack
        "pack_size": "1kg",
        "weight_grams": 1000,
    },
    {
        "name": "Crumbed Fish Sticks FIRST Grade — 1kg",
        "slug": "crumbed-fish-sticks-first-grade-1kg",
        "description": "First Grade crumbed fish sticks, 1kg. Premium fish, proper crumb coating.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 14000,   # R140 per pack
        "pack_size": "1kg",
        "weight_grams": 1000,
    },
    {
        "name": "Crumbed Fish Cakes 100g — 1kg (10 cakes)",
        "slug": "crumbed-fish-cakes-100g-1kg",
        "description": "Crumbed fish cakes, 100g each, 1kg pack (10 cakes). Ready to fry or bake.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 9000,    # R90 per pack
        "pack_size": "1kg (10 x 100g cakes)",
        "weight_grams": 1000,
    },
    {
        "name": "Crumbed Fish Cakes 50g — 1kg (20 cakes)",
        "slug": "crumbed-fish-cakes-50g-1kg",
        "description": "Crumbed fish cakes, 50g each, 1kg pack (20 cakes). Ideal for kids or starters.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 8000,    # R80 per pack
        "pack_size": "1kg (20 x 50g cakes)",
        "weight_grams": 1000,
    },
    {
        "name": "Prawn Meat 60/80 — 800g",
        "slug": "prawn-meat-60-80-800g",
        "description": "Peeled prawn meat, 60/80 count grade, 800g pack. Ready for curries, stir-fries, and pastas.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 17000,   # R170 per pack
        "pack_size": "800g",
        "weight_grams": 800,
    },
    {
        "name": "Crumbed Squid Strips Salt & Pepper — 400g",
        "slug": "crumbed-squid-strips-salt-pepper-400g",
        "description": "Salt and pepper crumbed squid strips, 400g. Fry until golden for a crowd-pleasing snack.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 9000,    # R90 per pack
        "pack_size": "400g",
        "weight_grams": 400,
    },
    {
        "name": "Argentinian Pink Prawns (whole) — 1kg",
        "slug": "argentinian-pink-prawns-whole-1kg",
        "description": "Argentinian pink prawns, whole, 1kg pack (L1 grade — large). Braai, garlic butter, or curry.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 26000,   # R260 per pack
        "pack_size": "1kg (L1 grade)",
        "weight_grams": 1000,
    },
    {
        "name": "Half Shell Mussels — 800g",
        "slug": "half-shell-mussels-800g",
        "description": "Frozen half-shell mussels, 800g. Steam in white wine and garlic in minutes.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 14500,   # R145 per pack
        "pack_size": "800g",
        "weight_grams": 800,
    },
    {
        "name": "Norwegian Salmon Portions — 5 x 200g",
        "slug": "norwegian-salmon-portions-5x200g",
        "description": "Norwegian salmon portions, 5 x 200g. Premium, skin-on, ready to pan-fry or bake.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 60000,   # R600/kg — 5x200g = 1kg
        "pack_size": "5 x 200g (1kg total)",
        "weight_grams": 1000,
        "notes": "R600/kg — pack is 1kg total (5 portions).",
    },
    {
        "name": "Smoked Trout Slices — 80g",
        "slug": "smoked-trout-slices-80g",
        "description": "Smoked trout slices, 80g pack. Ready to serve with cream cheese, capers, and lemon.",
        "category": "frozen_seafood",
        "sold_by": "pack",
        "price_cents": 8000,    # R80 per pack
        "pack_size": "80g",
        "weight_grams": 80,
    },

    # ══════════════════════════════════════════════════════════════════════════
    # EXTRAS
    # ══════════════════════════════════════════════════════════════════════════
    {
        "name": "Bone Broth by Think Eat Live — 800ml",
        "slug": "bone-broth-think-eat-live-800ml",
        "description": "Nutritious bone broth by Think Eat Live, 800ml. Available in Chicken or Beef.",
        "category": "extras",
        "sold_by": "pack",
        "price_cents": 9500,    # R95 per pack
        "pack_size": "800ml",
        "weight_grams": 800,
        "notes": "Specify Chicken or Beef when ordering.",
    },
]


def seed_tenant() -> None:
    db = get_tenant_db()
    db.upsert(
        tenant_id=TENANT_ID,
        company_name="Off the Hook",
        whatsapp=WHATSAPP_NUMBER,
        email="info@offthehook.capetown",
        plan="growth",
    )
    db.add_phone(TENANT_ID, WHATSAPP_NUMBER, label="orders", role="admin")
    print(f"✓ Tenant '{TENANT_ID}' registered — WhatsApp {WHATSAPP_NUMBER}")

    # Also register Stacy and Roland's numbers so they can manage the store via WhatsApp
    # Stacy: owner — receives order alerts and can approve sign-offs
    # Roland: operations — receives delivery and stock alerts
    db.add_phone(TENANT_ID, STACY_WHATSAPP, label="owner", role="admin")
    db.add_phone(TENANT_ID, ROLAND_WHATSAPP, label="operations", role="manager")
    print(f"✓ Team registered — Stacy {STACY_WHATSAPP} (owner), Roland {ROLAND_WHATSAPP} (operations)")


def seed_products() -> None:
    client = create_client(settings.supabase_url, settings.supabase_service_role_key or settings.supabase_service_key)
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for p in PRODUCTS:
        row = {
            "id": str(uuid.uuid4()),
            "tenant_id": TENANT_ID,
            "in_stock": True,
            "is_daily_catch": False,
            "sold_by": "pack",
            "created_at": now,
            "updated_at": now,
            "notes": None,
            "weight_grams": None,
            "serves": None,
            "image_url": None,
            "pack_size": None,
            "stock_quantity": None,
            **p,
        }
        rows.append(row)

    result = client.table("commerce_products").upsert(rows, on_conflict="tenant_id,slug").execute()
    print(f"✓ {len(result.data)} products seeded for '{TENANT_ID}'")

    # Print summary by category
    cats: dict[str, int] = {}
    for p in PRODUCTS:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    for cat, n in sorted(cats.items()):
        print(f"  {cat}: {n} products")


if __name__ == "__main__":
    print("Off the Hook — seeding tenant and products...")
    seed_tenant()
    seed_products()
    print("\nDone. Run migration 002_vula_commerce.sql in Supabase first if not done.")
