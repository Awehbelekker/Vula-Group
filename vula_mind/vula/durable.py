"""vula/durable.py — write-through of local SQLite stores to Supabase.

2026-09-27: Railway runs this service with NO volume attached (railway.toml declares /data, but
the service's volume list is empty), so every file under settings.data_dir — drafts.db,
tenants.db, the Takeoff rates and supplier databases — is wiped on every deploy. Each of those
stores keeps its SQLite file as a fast local cache and now also writes every change to a
Supabase table (migration 180), and re-reads from Supabase when the local copy is empty or
missing a row. Callers don't change.

Everything here is best-effort and never raises: with no Supabase configured (tests, local
dev) the stores behave exactly as before, SQLite only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _table(name: str):
    try:
        from vula.commerce.service import _client
        return _client().table(name)
    except Exception:  # noqa: BLE001 — not configured / unreachable → local only
        return None


def upsert(table: str, row: Dict[str, Any], on_conflict: Optional[str] = None) -> bool:
    t = _table(table)
    if t is None:
        return False
    try:
        (t.upsert(row, on_conflict=on_conflict) if on_conflict else t.upsert(row)).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("durable upsert to %s failed: %s", table, exc)
        return False


def delete(table: str, **eq: Any) -> bool:
    t = _table(table)
    if t is None:
        return False
    try:
        q = t.delete()
        for k, v in eq.items():
            q = q.eq(k, v)
        q.execute()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("durable delete from %s failed: %s", table, exc)
        return False


def select(table: str, columns: str = "*", order: Optional[str] = None, desc: bool = False,
           limit: Optional[int] = None, **eq: Any) -> Optional[List[Dict[str, Any]]]:
    """Rows, or None when Supabase isn't available (so callers can tell "none" from "unknown")."""
    t = _table(table)
    if t is None:
        return None
    try:
        q = t.select(columns)
        for k, v in eq.items():
            q = q.eq(k, v)
        if order:
            q = q.order(order, desc=desc)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("durable select from %s failed: %s", table, exc)
        return None
