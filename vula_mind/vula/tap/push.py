"""Web Push sender (VAPID) for the coach app. Needs `pywebpush` and the VAPID keys in settings;
without keys `build_pusher()` returns None and the app simply has no push (WhatsApp still alerts)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WebPusher:
    def __init__(self, private_key: str, subject: str):
        self.private_key, self.subject = private_key, subject

    def _send_sync(self, sub: dict, payload: dict) -> str:
        from pywebpush import WebPushException, webpush
        try:
            webpush(subscription_info={"endpoint": sub["endpoint"],
                                       "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                    data=json.dumps(payload), vapid_private_key=self.private_key,
                    vapid_claims={"sub": self.subject}, ttl=3600)
            return "ok"
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                return "gone"
            logger.warning("web push rejected (%s): %s", status, str(exc)[:120])
            return "error"

    async def send(self, sub: dict, payload: dict) -> str:
        return await asyncio.to_thread(self._send_sync, sub, payload)


def build_pusher(settings) -> Optional[WebPusher]:
    if settings.vapid_private_key and settings.vapid_public_key:
        return WebPusher(settings.vapid_private_key, settings.vapid_subject)
    return None
