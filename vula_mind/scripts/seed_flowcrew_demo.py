"""
scripts/seed_flowcrew_demo.py — FlowCrew's first demo business: Atlantic Surf Co. (Table View).

Sets up a PRIVATE demo of a surf school on Vula under the FlowCrew name, from what the shop's own
website publishes (read 24 Nov 2026 with tools/site_check.py: atlanticsurfco.co.za/surf-lesson-
prices-cape-town/ and /rentals/). It is not the shop's own account: it has no WhatsApp number of
theirs, sends nothing to their customers and is not listed anywhere. Move it to their real number
only once they have agreed.

What it does (each step is read back; safe to run again):
  1. the business `atlantic_surf_demo`, type `watersports`, if it isn't there yet;
  2. `features.partner = "flowcrew"` — the dashboard's credit reads "FlowCrew · Powered by Vula";
  3. the lessons, the gear for hire and their prices; the spot (Big Bay) for the conditions check.

Only surf lessons have published prices. SUP, skate and kite lessons are on the site without
prices, so they are left out rather than guessed — add them in Sell › Lessons › Lessons and prices.

Needs migration 303 applied. Run from vula_mind/:

    python scripts/seed_flowcrew_demo.py --dry-run     # prints what it would write
    python scripts/seed_flowcrew_demo.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TENANT_ID = "atlantic_surf_demo"
DISPLAY_NAME = "Atlantic Surf Co. (FlowCrew demo)"
PARTNER = "flowcrew"

# atlanticsurfco.co.za/surf-lesson-prices-cape-town/ — "2026 surf lesson prices". Cents.
SURF_LESSON = {
    "id": "surf", "name": "Surf lesson", "sport": "surf", "minutes": 90, "max_per_coach": 5, "package_sessions": 4,
    "about": "1 to 1.5 hours at Big Bay, soft-top board included",
    "group": [{"min": 1, "max": 1, "single_cents": 40000, "package_cents": 140000},
              {"min": 2, "max": 3, "single_cents": 35000, "package_cents": 120000},
              {"min": 4, "max": None, "single_cents": 30000, "package_cents": 100000}],
    "private": {"single_cents": 65000, "package_cents": 240000},
    "extras": [{"id": "wetsuit", "name": "Wetsuit", "cents": 5000}],
}
# atlanticsurfco.co.za/rentals/ — "Rental prices": 2 hours, 4 hours, 1 day, 2 days, 3 days, 4+ days per day.
HIRE = [
    {"id": "surfboard", "name": "Surfboard", "prices": {"2h": 15000, "4h": 25000, "1d": 30000, "2d": 56000, "3d": 78000}, "extra_day_cents": 24000},
    {"id": "premiumboard", "name": "Premium surfboard", "prices": {"2h": 30000, "4h": 50000, "1d": 80000}},
    {"id": "wetsuit", "name": "Wetsuit", "prices": {"2h": 7000, "4h": 9000, "1d": 10000, "2d": 18000, "3d": 24000}, "extra_day_cents": 7000},
    {"id": "bodyboard", "name": "Bodyboard", "prices": {"2h": 9000, "4h": 11000, "1d": 12000, "2d": 22000, "3d": 30000}, "extra_day_cents": 10000},
    {"id": "sup", "name": "SUP", "prices": {"2h": 25000, "4h": 35000, "1d": 40000, "2d": 76000, "3d": 108000}, "extra_day_cents": 34000},
    {"id": "skateboard", "name": "Skateboard", "prices": {"2h": 10000, "4h": 20000, "1d": 25000, "2d": 49000, "3d": 73000}, "extra_day_cents": 19000},
]
SETTINGS = {
    "enabled": True, "lessons": [SURF_LESSON], "hire": HIRE,
    "spot": {"name": "Big Bay", "lat": -33.794, "lng": 18.459},
    "form_url": "https://atlanticsurfco.co.za/atlantic-indemnity-waiver-form/",
    "pay_note": "Pay at the shop, 2 Coral Road, Table View.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    args = ap.parse_args()
    if args.dry_run:
        print(json.dumps({"tenant_id": TENANT_ID, "display_name": DISPLAY_NAME, "business_type": "watersports",
                          "features": {"partner": PARTNER}, "lesson_settings": SETTINGS}, indent=2))
        return 0

    from vula.api import tenants
    from vula.commerce import lessons
    db = tenants._client()
    rows = db.table("vula_tenant_config").select("tenant_id,features,business_type").eq("tenant_id", TENANT_ID).limit(1).execute().data or []
    if not rows:
        db.table("vula_tenant_config").insert({
            "tenant_id": TENANT_ID, "display_name": DISPLAY_NAME, "business_type": "watersports",
            "modules": tenants.BUSINESS_TYPES["watersports"]["modules"], "plan": "starter", "status": "active",
            "features": {"partner": PARTNER}, "updated_at": tenants._now()}).execute()
        tenants.ensure_billing_row(TENANT_ID, DISPLAY_NAME, plan="starter")
        print(f"✓ created {TENANT_ID} (watersports)")
    else:
        features = rows[0].get("features") if isinstance(rows[0].get("features"), dict) else {}
        if features.get("partner") != PARTNER:
            db.table("vula_tenant_config").update({"features": {**features, "partner": PARTNER}}).eq("tenant_id", TENANT_ID).execute()
        print(f"✓ {TENANT_ID} already there ({rows[0].get('business_type')})")
    tenants._CACHE.pop(TENANT_ID, None)
    cfg = tenants.get_config(TENANT_ID, fresh=True)
    if (tenants.partner_of(cfg) or {}).get("id") != PARTNER:
        print("✗ the FlowCrew partner tag didn't save")
        return 1
    print("✓ partner: FlowCrew")

    try:
        saved = lessons.save_settings(TENANT_ID, SETTINGS)          # checks every price and reads the row back
    except lessons.LessonError as exc:
        print(f"✗ lessons not saved: {exc} (is migration 303 applied?)")
        return 1
    print(f"✓ lessons on: {', '.join(x['name'] for x in saved['lessons'])}; hire: {', '.join(g['name'] for g in saved['hire'])}")
    print(lessons.price_list_text(TENANT_ID, saved))
    print("\nNext: give it a WhatsApp test number in Master › the tenant › WhatsApp (commerce mode), and add the team.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
