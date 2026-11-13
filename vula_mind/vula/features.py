"""Switches that can be on for one business before they are on for everyone.

12 Nov 2026. The new behaviour (the composed voice, client memory, private notes, stricter
grounding…) shipped behind global env settings, so trying one meant trying it on every business
at once. Each switch is now on for a business when EITHER the global setting is on (everyone) OR
that business's own `vula_tenant_config.features[name]` is true (migration 266) — so Off the Hook
or DIGG can pilot it for a week first. The operator sets the per-business switches in Master ›
a tenant › Overview › Features (PATCH /v1/master/tenants/{id}/features).

A business's config can't be read → only the global setting counts (off unless it is on for all).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# name → (the global setting, what it does — shown to the operator)
FEATURES: Dict[str, tuple] = {
    "voice_compose": ("voice_compose_enabled",
                      "Company voice, each person's own style and the channel, in every reply"),
    "follow_up_routing": ("follow_up_routing_enabled",
                          "Follow-ups keep their thread (the last question is given to the router)"),
    "verify_short_assertions": ("verify_short_assertions",
                                "Short answers backed by documents get the second check"),
    "strict_tenant_grounding": ("strict_tenant_grounding",
                                "Business facts with nothing on file get \"I don't have that\""),
    "client_memory": ("client_memory_enabled",
                      "A dated note per client, shown on their next message"),
    "personal_memory": ("personal_memory_enabled",
                        "Staff can ask Vula to remember things about them, privately"),
    "history_earlier_topics": ("history_earlier_topics_enabled",
                               "Staff threads carry their own messages from earlier in the week"),
    "advisor_mode": ("advisor_mode_enabled",
                     "The AI advisor for brainstorming and planning"),
}


def _global(name: str) -> bool:
    try:
        from config import settings
        return bool(getattr(settings, FEATURES[name][0], False))
    except Exception:  # noqa: BLE001
        return False


def _own(tenant_id: Optional[str]) -> Dict[str, bool]:
    if not tenant_id:
        return {}
    try:
        from vula.api.tenants import get_config
        f = (get_config(tenant_id) or {}).get("features")
    except Exception as exc:  # noqa: BLE001 — can't tell: only the global setting counts
        log.debug("features unreadable for %s: %s", tenant_id, exc)
        return {}
    return {k: bool(v) for k, v in f.items()} if isinstance(f, dict) else {}


def on(tenant_id: Optional[str], name: str) -> bool:
    """Is this switch on for this business? (On for everyone, or for this business.)"""
    if name not in FEATURES:
        raise KeyError(f"unknown feature {name!r}")
    return _global(name) or bool(_own(tenant_id).get(name))


def on_anywhere(name: str) -> bool:
    """On for everyone, or for at least one business — for background jobs that would otherwise
    do nothing (the client-memory sweep)."""
    if _global(name):
        return True
    try:
        from vula.commerce import service
        rows = service._client().table("vula_tenant_config").select("tenant_id,features").execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.debug("features scan skipped: %s", exc)
        return False
    return any(isinstance(r.get("features"), dict) and r["features"].get(name) for r in rows if isinstance(r, dict))


def listing(tenant_id: str) -> List[Dict[str, object]]:
    """Every switch, for the operator: on for this business, on for everyone."""
    own = _own(tenant_id)
    return [{"name": n, "label": label, "here": bool(own.get(n)), "everywhere": _global(n)}
            for n, (_s, label) in FEATURES.items()]


def validate(patch: Dict[str, object]) -> Dict[str, bool]:
    """Only known switch names, as booleans. ValueError on anything else."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Send {feature: true|false}.")
    unknown = sorted(k for k in patch if k not in FEATURES)
    if unknown:
        raise ValueError(f"Unknown feature(s): {', '.join(unknown)}. Known: {', '.join(FEATURES)}.")
    return {k: bool(v) for k, v in patch.items()}
