"""One cached read of a tenant's active team (vula_team_members), shared by the message hot path.

2026-09-29 (architecture review, PR B): every inbound WhatsApp message read the whole team table
3–5 times — _caller_identity, _is_tenant_owner, _sender_is_sales_rep, commerce_admin's
_member_access and yoco's alert list each ran their own select. One read per tenant per minute
now serves all of them.

A failed read raises and is never cached, so each caller keeps its own fallback exactly as before
(static maps, "treat as a customer"). Every write to the team through the API calls
invalidate(), so a newly added rep is recognised on their very next message; the 60s TTL only
bounds staleness for writes made outside the API (the Supabase editor).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TTL_SECONDS = 60.0
_COLUMNS = "id,name,whatsapp,role,access,notify,email"

_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_LOCK = threading.Lock()


def digits_za(phone: str) -> str:
    """Bare MSISDN digits for comparison (0XX… → 27XX…)."""
    n = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + n[1:] if n.startswith("0") else n


def active_members(tenant_id: str) -> List[Dict[str, Any]]:
    """The tenant's active team rows (copies). Raises if the DB read fails — nothing cached."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(tenant_id)
        if hit and now - hit[0] < TTL_SECONDS:
            return [dict(r) for r in hit[1]]
    from vula.commerce import service
    rows = (service._client().table("vula_team_members").select(_COLUMNS)
            .eq("tenant_id", tenant_id).eq("active", True).execute().data)
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    with _LOCK:
        _CACHE[tenant_id] = (now, rows)
    return [dict(r) for r in rows]


def member_for_phone(tenant_id: str, phone: Optional[str]) -> Optional[Dict[str, Any]]:
    """The active member whose WhatsApp number is `phone`, or None. Raises on a failed read."""
    target = digits_za(phone or "")
    if not target:
        return None
    return next((r for r in active_members(tenant_id)
                 if digits_za(r.get("whatsapp") or "") == target), None)


def invalidate(tenant_id: Optional[str] = None) -> None:
    """Drop the cached team for one tenant (or all) after a write."""
    with _LOCK:
        if tenant_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(tenant_id, None)
