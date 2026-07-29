"""
tools/backfill_credential_encryption.py — one-off, human-run re-encryption of existing
plaintext rows in the 4 credential tables (migration 112 / security remediation Phase 1).

vula/google/credentials.py, vula/microsoft/credentials.py, vula/api/whatsapp_connect.py,
and vula/api/clickup.py now encrypt tokens on every NEW write, and every read site already
decrypts via vula.email_imap.credentials.decrypt_secret() (which passes plaintext legacy
values through unchanged) — so nothing breaks if this script is never run. This script
just closes the gap for rows written before that change landed, so they're encrypted at
rest too rather than waiting on a token to naturally rotate.

Usage:
    python tools/backfill_credential_encryption.py            # dry run — reports counts only
    python tools/backfill_credential_encryption.py --apply    # actually re-encrypts

Run with prod Supabase credentials in the environment (same as any other script here).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vula.email_imap.credentials import encrypt_secret  # noqa: E402


def _client():
    from config import settings
    from supabase import create_client
    return create_client(settings.supabase_url,
                         settings.supabase_service_role_key or settings.supabase_service_key)


def _is_encrypted(value: str | None) -> bool:
    return bool(value) and (value.startswith("fernet:") or value.startswith("plain:"))


# (table, [token columns to check/encrypt], primary key column)
TARGETS = [
    ("vula_google_accounts", ["access_token", "refresh_token"], "id"),
    ("vula_microsoft_accounts", ["access_token", "refresh_token"], "id"),
    ("vula_whatsapp_accounts", ["access_token"], "id"),
    ("vula_clickup_accounts", ["api_token"], "id"),
]


def backfill(apply: bool) -> None:
    db = _client()
    total_plaintext = 0
    total_rows = 0
    for table, cols, pk in TARGETS:
        rows = db.table(table).select(",".join([pk] + cols) + ",tenant_id").execute().data or []
        table_plaintext = 0
        for row in rows:
            total_rows += 1
            patch = {}
            for col in cols:
                val = row.get(col)
                if val and not _is_encrypted(val):
                    patch[col] = encrypt_secret(val)
            if patch:
                table_plaintext += 1
                total_plaintext += 1
                if apply:
                    db.table(table).update(patch).eq(pk, row[pk]).execute()
                    print(f"  [applied]  {table} tenant={row.get('tenant_id')} "
                         f"columns={list(patch.keys())}")
                else:
                    print(f"  [would fix] {table} tenant={row.get('tenant_id')} "
                         f"columns={list(patch.keys())}")
        print(f"{table}: {table_plaintext}/{len(rows)} row(s) needed encryption")
    print()
    print(f"TOTAL: {total_plaintext}/{total_rows} row(s) across 4 tables "
         f"{'re-encrypted' if apply else 'need re-encryption (dry run — nothing changed)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually write re-encrypted values (default is dry-run/report-only).")
    args = parser.parse_args()
    backfill(apply=args.apply)
