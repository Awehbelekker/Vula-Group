"""
tools/import_oth_contacts.py — one-time importer for Off the Hook's area contact lists into
commerce_contacts (migration 046).

Reads the 14 canonical CSVs (ignores the (1)/(2)/-Copy duplicate files), fixes encoding, normalizes
phones to 27XXXXXXXXX, dedups by phone (merging area/product/group tags), and upserts into
commerce_contacts with consent_status='unknown' — so nothing can broadcast to them until they opt in.

Usage:
    python tools/import_oth_contacts.py --dir "C:/Users/Ian/Downloads" --tenant off-the-hook          # dry-run report
    python tools/import_oth_contacts.py --dir "C:/Users/Ian/Downloads" --tenant off-the-hook --commit  # write

Idempotent: upserts on (tenant_id, phone), so re-running updates rather than duplicating.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

# Make `vula` / `config` importable no matter where this is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CANONICAL = ["atlantic-beach", "big-bay", "CHICKEN ONLY", "edgemead milnerton", "flamingo-vlei",
             "melkbos", "off-the-hook", "parklands", "Southern Subs", "sunningdale", "sunset-beach",
             "tableview", "TUNA ONLY", "West Beach"]

# Group/interest tags to lift out of the free-text name/notes fields.
TAG_KEYWORDS = {
    "lcc": "lcc", "wcbac": "wcbac", "remax": "remax", "recovery": "recovery",
    "collect": "collect", "bone broth": "bone-broth", "nutrition": "nutrition",
    "herbalife": "herbalife", "tuna time": "tuna",
}


def product_of(fname: str) -> str:
    n = fname.lower()
    return "chicken" if "chicken" in n else "tuna" if "tuna" in n else "fish"


def norm_phone(p: str):
    """Return (normalized, kind). kind: za | intl | corrupted | empty | suspect."""
    if not p or not p.strip():
        return None, "empty"
    p = p.strip()
    if re.search(r"[eE]\+?\d", p):          # Excel scientific-notation corruption — unrecoverable
        return None, "corrupted"
    d = re.sub(r"\D", "", p)
    if not d:
        return None, "empty"
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) == 10:
        d = "27" + d[1:]
    elif len(d) == 9:
        d = "27" + d
    elif d.startswith("270") and len(d) == 12:   # "+27 (072) …" — stray 0 after country code
        d = "27" + d[3:]
    if d.startswith("27") and len(d) == 11:
        return d, "za"
    if d.startswith(("44", "43", "31", "49", "61", "971", "1")) and 10 <= len(d) <= 14:
        return d, "intl"
    return d, "suspect"


def extract_tags(text: str) -> list[str]:
    t = (text or "").lower()
    return sorted({v for k, v in TAG_KEYWORDS.items() if k in t})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder holding the canonical CSVs")
    ap.add_argument("--tenant", default="off-the-hook")
    ap.add_argument("--commit", action="store_true", help="write to DB (default: dry-run report)")
    args = ap.parse_args(argv)

    merged: dict[str, dict] = {}
    flagged: list[dict] = []
    read = 0
    for base in CANONICAL:
        path = os.path.join(args.dir, base + ".csv")
        if not os.path.exists(path):
            print(f"  (skip, not found: {base}.csv)")
            continue
        area, product = base, product_of(base)
        with open(path, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                read += 1
                name = (row.get("Full Name") or row.get("FULL NAME") or "").strip()
                raw_phone = row.get("Phone Number") or row.get("CELL NUMBER") or ""
                email = (row.get("Email Address") or "").strip()
                address = (row.get("Physical Address") or row.get("PHYSICAL ADDRESS") or "").strip()
                blob = " ".join(str(row.get(k) or "") for k in
                                ("Full Name", "First Name", "Last Name", "SURNAME", "Notes"))
                if not (name or raw_phone.strip()):
                    continue
                phone, kind = norm_phone(raw_phone)
                if kind not in ("za", "intl"):
                    flagged.append({"name": name, "raw_phone": raw_phone, "kind": kind, "area": area})
                    continue
                c = merged.setdefault(phone, {
                    "tenant_id": args.tenant, "phone": phone, "name": name, "email": email,
                    "area": area, "product": product, "address": address,
                    "consent_status": "unknown", "source": "import", "tags": set(),
                })
                if name and not c["name"]:
                    c["name"] = name
                if email and not c["email"]:
                    c["email"] = email
                if address and not c["address"]:
                    c["address"] = address
                # A specific area beats the general "off-the-hook" list.
                if c["area"] == "off-the-hook" and area != "off-the-hook":
                    c["area"] = area
                c["tags"].update(extract_tags(blob))
                c["tags"].add(area)
                c["tags"].add(product)

    rows = []
    for c in merged.values():
        c["tags"] = sorted(c["tags"])
        rows.append(c)

    print(f"\ncanonical files    : {len(CANONICAL)}")
    print(f"rows read          : {read}")
    print(f"unique contacts    : {len(rows)}")
    print(f"flagged (not imported): {len(flagged)}  -> {[f['raw_phone'] or '(empty)' for f in flagged]}")
    print(f"with email         : {sum(1 for c in rows if c['email'])}")

    if not args.commit:
        print("\nDRY-RUN — nothing written. Re-run with --commit to upsert.")
        for c in rows[:5]:
            print("  sample:", {k: c[k] for k in ("phone", "name", "area", "product", "tags")})
        return 0

    from vula.commerce import service
    db = service._client()
    n = 0
    for i in range(0, len(rows), 200):
        batch = rows[i:i + 200]
        db.table("commerce_contacts").upsert(batch, on_conflict="tenant_id,phone").execute()
        n += len(batch)
    print(f"\nUPSERTED {n} contacts into commerce_contacts (consent_status='unknown').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
