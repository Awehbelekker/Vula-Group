"""
scripts/seed_awake_sa.py — Register Awake SA as a Vula Commerce tenant.

Awake SA is the internal Vula Commerce reference client — eFoil / watersports
hardware. Already has its own storefront at github.com/Awehbelekker/Awake-South-Africa.
This script registers it in the Vula tenant DB so the Vula backend serves it.

Run once:
    cd vula_mind
    python scripts/seed_awake_sa.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from vula.models.tenants import get_tenant_db

TENANT_ID = "awake-sa"
WHATSAPP_NUMBER = "0820000000"  # Replace with Awake SA's actual WhatsApp once confirmed


def seed_tenant() -> None:
    db = get_tenant_db()
    db.upsert(
        tenant_id=TENANT_ID,
        company_name="Awake South Africa",
        whatsapp=WHATSAPP_NUMBER,
        email="richard@awakesa.co.za",
        plan="growth",
    )
    db.add_phone(TENANT_ID, WHATSAPP_NUMBER, label="orders", role="admin")
    print(f"✓ Tenant '{TENANT_ID}' registered")
    print("  Update WHATSAPP_NUMBER in this script once Awake SA's number is confirmed.")


if __name__ == "__main__":
    seed_tenant()
