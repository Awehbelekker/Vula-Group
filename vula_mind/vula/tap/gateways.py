"""Which payment gateway a business's Tap to Pay checks out through (2026-10-28).

PayFast was wired in directly. Stitch is next (Pay by Bank / PayShap, cards, wallets), so the
service now talks to a router: checkout goes to the gateway the business chose, and a payment
notification is verified by the gateway it came from (its webhook path names the provider). A
business's choice is its default connected payment provider when that provider can do Tap to Pay,
else PayFast — so every business keeps PayFast until another gateway is built and connected.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

DEFAULT = "payfast"
CHOICE_TTL_S = 60.0          # the tap page is on the till's critical path: one lookup a minute


class GatewayRouter:
    def __init__(self, gateways: Dict[str, Any], choose: Optional[Callable[[str], Optional[str]]] = None,
                 ttl_s: float = 0.0, clock: Callable[[], float] = time.monotonic):
        self.gateways = gateways
        self.choose = choose or (lambda tenant_id: None)
        self.ttl_s, self.clock = ttl_s, clock
        self._seen: Dict[str, tuple] = {}

    def name_for(self, tenant_id: str) -> str:
        hit = self._seen.get(tenant_id)
        if hit and self.clock() - hit[1] < self.ttl_s:
            return hit[0]
        try:
            name = self.choose(tenant_id)
        except Exception as exc:  # noqa: BLE001 — a lookup failure falls back, never blocks a payment
            log.debug("tap gateway choice failed for %s: %s", tenant_id, exc)
            name = None
        name = name if name in self.gateways else (DEFAULT if DEFAULT in self.gateways else next(iter(self.gateways)))
        if self.ttl_s:
            self._seen[tenant_id] = (name, self.clock())
        return name

    def label_for(self, tenant_id: str) -> str:
        g = self.gateways[self.name_for(tenant_id)]
        return getattr(g, "label", "") or self.name_for(tenant_id).title()

    async def create_checkout(self, *, tenant_id: str, session: dict, description: str) -> Optional[str]:
        return await self.gateways[self.name_for(tenant_id)].create_checkout(
            tenant_id=tenant_id, session=session, description=description)

    async def verify_itn(self, *, tenant_id: str, headers: dict, body: bytes, form: dict,
                         provider: str = DEFAULT) -> Optional[dict]:
        g = self.gateways.get(provider)
        if g is None:
            return None
        return await g.verify_itn(tenant_id=tenant_id, headers=headers, body=body, form=form, provider=provider)


def tenant_choice(tenant_id: str, capable: set) -> Optional[str]:
    """The business's default connected payment provider, when it can do Tap to Pay."""
    from vula import payments
    try:
        rows = [r for r in payments.list_providers(tenant_id) if r.get("active", True) is not False]
    except Exception as exc:  # noqa: BLE001
        log.debug("payment providers read failed for %s: %s", tenant_id, exc)
        return None
    able = [r for r in rows if r.get("provider") in capable]
    pick = next((r for r in able if r.get("is_default")), able[0] if able else None)
    return pick.get("provider") if pick else None
