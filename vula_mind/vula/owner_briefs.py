"""
vula/owner_briefs.py — each business's own on/off for the owner briefs on WhatsApp.

  morning   the weekday-morning brief (vula/owner_digest.py): money plus what needs a person today
  weekly    the Monday advisor (vula/owner_advisor.py)

Stored in vula_tenant_config.owner_briefs (migration 257) as {"morning": bool, "weekly": bool}.
A business that hasn't chosen follows the platform default (settings.owner_digest_enabled /
settings.owner_advisor_enabled), so turning the default on reaches every business that hasn't
said no, and a business can switch either brief on or off for itself at any time.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

KINDS = ("morning", "weekly")


def platform_default(kind: str) -> bool:
    from config import settings
    return bool(settings.owner_digest_enabled if kind == "morning" else settings.owner_advisor_enabled)


def saved_for(tenant_id: str) -> Dict[str, bool]:
    """Only what the business itself chose — a missing key means "follow the default"."""
    try:
        from vula.api.tenants import get_config
        raw = get_config(tenant_id).get("owner_briefs") or {}
    except Exception as exc:
        log.debug("owner brief settings unread (run migration 257?): %s", exc)
        raw = {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in KINDS and isinstance(v, bool)}


def enabled(tenant_id: str, kind: str, saved: Optional[Dict[str, bool]] = None) -> bool:
    saved = saved_for(tenant_id) if saved is None else saved
    return saved[kind] if kind in saved else platform_default(kind)


def settings_for(tenant_id: str) -> Dict[str, Any]:
    saved = saved_for(tenant_id)
    return {"morning": enabled(tenant_id, "morning", saved), "weekly": enabled(tenant_id, "weekly", saved),
            "chosen": sorted(saved), "defaults": {k: platform_default(k) for k in KINDS}}


def save(tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the business's choice; None for a kind goes back to following the default."""
    from vula.api import tenants as _t
    new = dict(saved_for(tenant_id))
    for k in KINDS:
        if k not in (patch or {}):
            continue
        v = patch[k]
        if v is None:
            new.pop(k, None)
        else:
            new[k] = bool(v)
    (_t._client().table("vula_tenant_config").update({"owner_briefs": new or None})
     .eq("tenant_id", tenant_id).execute())
    _t.invalidate(tenant_id)
    return settings_for(tenant_id)
