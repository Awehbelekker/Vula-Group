"""
vula/models/tenants.py

Local SQLite tenant registry.

Used as primary store when Supabase is not configured, and as fallback
cache when it is.  Lets the full stack run locally without any cloud dependency.

Usage:
    from vula.models.tenants import get_tenant_db

    db = get_tenant_db()
    db.upsert("digg-demo", "DIGG Architecture", "27827077080", "judy@digg.co.za")
    tenant_id = db.lookup_by_phone("27827077080")  # → "digg-demo"
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from config import settings


def _normalise_phone(phone: str) -> str:
    """Strip formatting and normalise to 27XXXXXXXXX format."""
    p = re.sub(r"[\s\-+()\.]", "", phone)
    if p.startswith("0") and len(p) == 10:
        p = "27" + p[1:]
    return p


class LocalTenantDB:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (settings.data_dir / "tenants.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id    TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    whatsapp     TEXT,
                    email        TEXT,
                    plan         TEXT DEFAULT 'starter',
                    status       TEXT DEFAULT 'active',
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenants_phone ON tenants(whatsapp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenants_email ON tenants(email)")
            conn.commit()

    def upsert(self, tenant_id: str, company_name: str,
               whatsapp: Optional[str] = None,
               email: Optional[str] = None,
               plan: str = "starter") -> dict:
        """Create or update a tenant record."""
        normalised = _normalise_phone(whatsapp) if whatsapp else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tenants (tenant_id, company_name, whatsapp, email, plan)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                       company_name = excluded.company_name,
                       whatsapp     = excluded.whatsapp,
                       email        = excluded.email,
                       plan         = excluded.plan""",
                (tenant_id, company_name, normalised, email, plan),
            )
            conn.commit()
        return self.get(tenant_id)

    def get(self, tenant_id: str) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT tenant_id, company_name, whatsapp, email, plan, status FROM tenants WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        if not row:
            return None
        return {"tenant_id": row[0], "company_name": row[1], "whatsapp": row[2],
                "email": row[3], "plan": row[4], "status": row[5]}

    def lookup_by_phone(self, phone: str) -> Optional[str]:
        """Return tenant_id for a given phone number (any common format)."""
        normalised = _normalise_phone(phone)
        variants = {normalised}
        # also try local 0XX format
        if normalised.startswith("27") and len(normalised) == 11:
            variants.add("0" + normalised[2:])

        with sqlite3.connect(self.db_path) as conn:
            for v in variants:
                row = conn.execute(
                    "SELECT tenant_id FROM tenants WHERE whatsapp = ? AND status = 'active'",
                    (v,),
                ).fetchone()
                if row:
                    return row[0]
        return None

    def list_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT tenant_id, company_name, whatsapp, email, plan, status FROM tenants ORDER BY created_at DESC"
            ).fetchall()
        return [{"tenant_id": r[0], "company_name": r[1], "whatsapp": r[2],
                 "email": r[3], "plan": r[4], "status": r[5]} for r in rows]


_db: Optional[LocalTenantDB] = None


def get_tenant_db() -> LocalTenantDB:
    global _db
    if _db is None:
        _db = LocalTenantDB()
    return _db
