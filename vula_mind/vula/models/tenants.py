"""
vula/models/tenants.py

Local SQLite tenant registry — supports multiple phone numbers per tenant.

A tenant (e.g. DIGG) can have many authorised numbers.  Any of them can
message Vula and be resolved to the same tenant and knowledge base.

Usage:
    db = get_tenant_db()

    # Register tenant with a primary number
    db.upsert("digg-demo", "DIGG Architecture", "0827077080", "judy@digg.co.za")

    # Add extra numbers at any time
    db.add_phone("digg-demo", "0673636081", label="vula-business")
    db.add_phone("digg-demo", "0827077080", label="judy-personal")

    # Lookup works for any registered number
    db.lookup_by_phone("+27827077080")   # → "digg-demo"
    db.lookup_by_phone("0673636081")     # → "digg-demo"

    # List all numbers for a tenant
    db.phones("digg-demo")
    # → [{"phone": "27673636081", "label": "vula-business"}, ...]
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from config import settings


def _normalise(phone: str) -> str:
    """Strip formatting → 27XXXXXXXXX (no leading +)."""
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
                    email        TEXT,
                    plan         TEXT DEFAULT 'starter',
                    status       TEXT DEFAULT 'active',
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_phones (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                    phone      TEXT NOT NULL,
                    label      TEXT,
                    role       TEXT NOT NULL DEFAULT 'staff',
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(phone)
                )
            """)
            # Migrate existing rows that pre-date the role column
            try:
                conn.execute("ALTER TABLE tenant_phones ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'")
            except Exception:
                pass  # column already exists
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phones_phone     ON tenant_phones(phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phones_tenant_id ON tenant_phones(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenants_email    ON tenants(email)")
            conn.commit()

    # ── Tenants ───────────────────────────────────────────────────────────────

    def upsert(self, tenant_id: str, company_name: str,
               whatsapp: Optional[str] = None,
               email: Optional[str] = None,
               plan: str = "starter") -> dict:
        """Create or update a tenant.  Registers whatsapp as primary number if given."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tenants (tenant_id, company_name, email, plan)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                       company_name = excluded.company_name,
                       email        = COALESCE(excluded.email, tenants.email),
                       plan         = excluded.plan""",
                (tenant_id, company_name, email, plan),
            )
            conn.commit()

        if whatsapp:
            self.add_phone(tenant_id, whatsapp, label="primary")

        return self.get(tenant_id)

    def get(self, tenant_id: str) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT tenant_id, company_name, email, plan, status FROM tenants WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        if not row:
            return None
        record = {"tenant_id": row[0], "company_name": row[1],
                  "email": row[2], "plan": row[3], "status": row[4]}
        record["phones"] = self.phones(tenant_id)
        return record

    def list_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT tenant_id, company_name, email, plan, status FROM tenants ORDER BY created_at DESC"
            ).fetchall()
        return [self.get(r[0]) for r in rows]

    # ── Phone numbers ─────────────────────────────────────────────────────────

    def add_phone(self, tenant_id: str, phone: str,
                  label: Optional[str] = None,
                  role: str = "staff") -> None:
        """Register a phone number for a tenant.

        role options:
          admin  — full access: KB queries + document upload + task approval
          staff  — KB queries only (can ask questions, cannot approve tasks)
          viewer — read-only KB (future use)

        Contractors (DONE/photos/sign-off) are NOT registered here — they
        live in the field_ops contractors table instead.
        """
        if role not in ("admin", "staff", "viewer"):
            raise ValueError(f"role must be admin, staff, or viewer — got '{role}'")
        normalised = _normalise(phone)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tenant_phones (tenant_id, phone, label, role)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(phone) DO UPDATE SET
                       tenant_id = excluded.tenant_id,
                       label     = COALESCE(excluded.label, tenant_phones.label),
                       role      = excluded.role""",
                (tenant_id, normalised, label, role),
            )
            conn.commit()

    def remove_phone(self, phone: str) -> None:
        """Deregister a phone number."""
        normalised = _normalise(phone)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM tenant_phones WHERE phone = ?", (normalised,))
            conn.commit()

    def phones(self, tenant_id: str) -> list[dict]:
        """Return all registered numbers for a tenant."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT phone, label, role FROM tenant_phones WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()
        return [{"phone": r[0], "label": r[1], "role": r[2]} for r in rows]

    def lookup_by_phone(self, phone: str) -> Optional[str]:
        """Return tenant_id for any registered number (any format)."""
        result = self.lookup_by_phone_with_role(phone)
        return result["tenant_id"] if result else None

    def lookup_by_phone_with_role(self, phone: str) -> Optional[dict]:
        """Return {tenant_id, role} for any registered number, or None.

        Checks local SQLite first (fast), then Supabase vula_tenant_phones
        (shared cloud state — works across Railway containers).
        """
        normalised = _normalise(phone)
        variants = {normalised}
        if normalised.startswith("27") and len(normalised) == 11:
            variants.add("0" + normalised[2:])

        # 1. Local SQLite
        with sqlite3.connect(self.db_path) as conn:
            for v in variants:
                row = conn.execute(
                    """SELECT tp.tenant_id, tp.role FROM tenant_phones tp
                       JOIN tenants t ON t.tenant_id = tp.tenant_id
                       WHERE tp.phone = ? AND t.status = 'active'""",
                    (v,),
                ).fetchone()
                if row:
                    return {"tenant_id": row[0], "role": row[1]}

        # 2. Supabase fallback (cloud state — required on Railway)
        result = self._supabase_lookup(variants)
        if result:
            return result
        return None

    def _supabase_lookup(self, variants: set[str]) -> Optional[dict]:
        """Look up phone in Supabase vula_tenant_phones. Returns None if unavailable."""
        _PLACEHOLDER = "your-project.supabase.co"
        if (not settings.supabase_url or _PLACEHOLDER in settings.supabase_url
                or not settings.supabase_service_key):
            return None
        try:
            import httpx
            for v in variants:
                resp = httpx.get(
                    f"{settings.supabase_url}/rest/v1/vula_tenant_phones",
                    params={"phone": f"eq.{v}", "select": "tenant_id,role", "limit": "1"},
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                    },
                    timeout=5.0,
                )
                if resp.is_success:
                    rows = resp.json()
                    if rows:
                        return {"tenant_id": rows[0]["tenant_id"], "role": rows[0]["role"]}
        except Exception:
            pass
        return None


_db: Optional[LocalTenantDB] = None


def get_tenant_db() -> LocalTenantDB:
    global _db
    if _db is None:
        _db = LocalTenantDB()
    return _db
