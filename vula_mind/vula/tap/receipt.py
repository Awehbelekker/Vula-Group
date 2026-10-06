"""Receipt links: /r/<token>. token = b64(payment_id:nonce) + "." + b64(HMAC-SHA256(secret, payload)[:16]).

Unguessable (128-bit MAC), stateless (nothing stored), revocable (DB nonce / revoked flag are checked
by the caller). The public receipt shows only what the customer already knows — never phone numbers,
the staff share or anything about other payments."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _mac(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:16])


def make_token(secret: str, payment_id: str, nonce: int = 0) -> str:
    payload = f"{payment_id}:{int(nonce)}"
    return f"{_b64(payload.encode())}.{_mac(secret, payload)}"


def read_token(secret: str, token: str) -> Optional[tuple[str, int]]:
    """(payment_id, nonce) when the MAC is valid, else None. Never raises."""
    try:
        body, mac = token.split(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        if not hmac.compare_digest(mac, _mac(secret, payload)):
            return None
        pid, nonce = payload.rsplit(":", 1)
        return pid, int(nonce)
    except Exception:  # noqa: BLE001
        return None


def vat_included(bill_cents: int) -> int:
    """VAT contained in a VAT-inclusive bill at 15%, to the cent. Tips are deliberately excluded:
    their VAT treatment is an open accountant question (docs/tap-to-pay.md)."""
    return (bill_cents * 15 + 57) // 115
