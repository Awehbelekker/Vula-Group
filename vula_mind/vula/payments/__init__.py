"""
vula/payments — pluggable SA payment gateways.

Each tenant connects one or more gateways (Yoco, PayFast, Peach, iKhokha, Ozow, Paystack)
in vula_payment_providers; one is the default used for invoice pay-links + checkout. A
provider implements create_link() (returns a hosted pay URL) and verify_webhook() (confirms
a paid event). Money is always integer cents; providers convert as their API requires.

Credentials live server-side only (vula_payment_providers.credentials, RLS-isolated).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)


# Per-provider credential fields the dashboard collects (documents the connect form).
PROVIDER_FIELDS = {
    "yoco":     ["secret_key"],
    "payfast":  ["merchant_id", "merchant_key", "passphrase"],
    "peach":    ["entity_id", "access_token", "webhook_secret"],
    "ikhokha":  ["app_id", "app_secret"],
    "ozow":     ["site_code", "private_key", "api_key"],
    "paystack": ["secret_key"],
}
PROVIDER_LABELS = {
    "yoco": "Yoco", "payfast": "PayFast", "peach": "Peach Payments",
    "ikhokha": "iKhokha", "ozow": "Ozow (instant EFT)", "paystack": "Paystack",
}


@dataclass
class PayLink:
    url: str
    provider: str
    reference: str
    raw: dict = field(default_factory=dict)


def _rand(cents: int) -> str:
    return f"{(int(cents) / 100):.2f}"


# ── Providers ─────────────────────────────────────────────────────────────────

class _Provider:
    name = ""

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode) -> PayLink:
        raise NotImplementedError

    async def verify_webhook(self, creds, headers, body: bytes, form: dict) -> Optional[dict]:
        """Return {'reference': str, 'paid': bool, 'raw': dict} for a valid event, else None."""
        return None


class Yoco(_Provider):
    name = "yoco"

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post("https://payments.yoco.com/api/checkouts",
                headers={"Authorization": f"Bearer {creds['secret_key']}", "Content-Type": "application/json"},
                json={"amount": int(amount_cents), "currency": "ZAR",
                      "successUrl": success_url, "cancelUrl": cancel_url, "failureUrl": cancel_url,
                      "metadata": {"reference": reference, **(customer or {})}})
        r.raise_for_status()
        d = r.json()
        return PayLink(url=d["redirectUrl"], provider=self.name, reference=reference, raw=d)

    async def verify_webhook(self, creds, headers, body, form):
        # HMAC handled in the existing yoco webhook; here we just read the event.
        try:
            data = json.loads(body or b"{}")
        except Exception:
            return None
        payload = data.get("payload", data)
        meta = payload.get("metadata", {})
        if data.get("type") in ("payment.succeeded", "checkout.completed"):
            return {"reference": meta.get("reference") or meta.get("invoice_id"), "paid": True, "raw": data}
        return None


class PayFast(_Provider):
    name = "payfast"

    def _sig(self, data: dict, passphrase: str) -> str:
        parts = [f"{k}={quote_plus(str(v).strip())}" for k, v in data.items() if v not in (None, "")]
        s = "&".join(parts)
        if passphrase:
            s += f"&passphrase={quote_plus(passphrase.strip())}"
        return hashlib.md5(s.encode()).hexdigest()

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        base = "https://sandbox.payfast.co.za" if mode == "test" else "https://www.payfast.co.za"
        data = {
            "merchant_id": creds["merchant_id"], "merchant_key": creds["merchant_key"],
            "return_url": success_url, "cancel_url": cancel_url, "notify_url": notify_url,
            "m_payment_id": reference, "amount": _rand(amount_cents),
            "item_name": (description or "Invoice")[:100],
        }
        if (customer or {}).get("email"):
            data["email_address"] = customer["email"]
        data["signature"] = self._sig(data, creds.get("passphrase", ""))
        qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in data.items())
        return PayLink(url=f"{base}/eng/process?{qs}", provider=self.name, reference=reference, raw=data)

    async def verify_webhook(self, creds, headers, body, form):
        d = form or {}
        # Signature check (order as received, excluding 'signature').
        check = {k: v for k, v in d.items() if k != "signature"}
        expect = self._sig(check, creds.get("passphrase", ""))
        if d.get("signature") and d["signature"] != expect:
            logger.warning("PayFast ITN signature mismatch")
            return None
        paid = (d.get("payment_status") == "COMPLETE")
        return {"reference": d.get("m_payment_id"), "paid": paid, "raw": d}


class Paystack(_Provider):
    name = "paystack"

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post("https://api.paystack.co/transaction/initialize",
                headers={"Authorization": f"Bearer {creds['secret_key']}", "Content-Type": "application/json"},
                json={"amount": int(amount_cents), "currency": "ZAR", "reference": reference,
                      "email": (customer or {}).get("email") or "customer@vula-ai.com",
                      "callback_url": success_url})
        r.raise_for_status()
        d = r.json()
        return PayLink(url=d["data"]["authorization_url"], provider=self.name, reference=reference, raw=d)

    async def verify_webhook(self, creds, headers, body, form):
        sig = headers.get("x-paystack-signature")
        expect = hmac.new(creds["secret_key"].encode(), body, hashlib.sha512).hexdigest()
        if not sig or not hmac.compare_digest(sig, expect):
            return None
        try:
            data = json.loads(body or b"{}")
        except Exception:
            return None
        if data.get("event") == "charge.success":
            return {"reference": data.get("data", {}).get("reference"), "paid": True, "raw": data}
        return None


class Ozow(_Provider):
    name = "ozow"

    def _hash(self, fields: list, private_key: str) -> str:
        s = "".join(str(f) for f in fields) + private_key
        return hashlib.sha512(s.lower().encode()).hexdigest()

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        is_test = "true" if mode == "test" else "false"
        amount = _rand(amount_cents)
        bank_ref = (description or "Invoice")[:20]
        body = {
            "countryCode": "ZA", "currencyCode": "ZAR", "amount": amount,
            "transactionReference": reference, "bankReference": bank_ref,
            "cancelUrl": cancel_url, "errorUrl": cancel_url, "successUrl": success_url,
            "notifyUrl": notify_url, "siteCode": creds["site_code"], "isTest": is_test,
        }
        body["hashCheck"] = self._hash(
            [body["siteCode"], body["countryCode"], body["currencyCode"], body["amount"],
             body["transactionReference"], body["bankReference"], body["cancelUrl"],
             body["errorUrl"], body["successUrl"], body["notifyUrl"], body["isTest"]],
            creds["private_key"])
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post("https://api.ozow.com/postpaymentrequest",
                headers={"ApiKey": creds["api_key"], "Accept": "application/json", "Content-Type": "application/json"},
                json=body)
        r.raise_for_status()
        d = r.json()
        return PayLink(url=d.get("url") or d.get("paymentRequestId"), provider=self.name, reference=reference, raw=d)

    async def verify_webhook(self, creds, headers, body, form):
        d = form or {}
        paid = (str(d.get("Status", "")).lower() == "complete")
        return {"reference": d.get("TransactionReference"), "paid": paid, "raw": d}


class Peach(_Provider):
    name = "peach"

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        base = "https://testsecure.peachpayments.com" if mode == "test" else "https://secure.peachpayments.com"
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(f"{base}/checkout/initiate",
                headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"},
                json={"entityId": creds["entity_id"], "amount": _rand(amount_cents), "currency": "ZAR",
                      "merchantTransactionId": reference, "shopperResultUrl": success_url,
                      "notificationUrl": notify_url})
        r.raise_for_status()
        d = r.json()
        url = d.get("redirectUrl") or (d.get("checkoutId") and f"{base}/checkout/{d['checkoutId']}")
        return PayLink(url=url, provider=self.name, reference=reference, raw=d)

    async def verify_webhook(self, creds, headers, body, form):
        try:
            data = json.loads(body or b"{}") if body else (form or {})
        except Exception:
            data = form or {}
        code = str((data.get("result") or {}).get("code", data.get("resultCode", "")))
        paid = code.startswith("000.000.") or code.startswith("000.100.1")
        return {"reference": data.get("merchantTransactionId"), "paid": paid, "raw": data}


class IKhokha(_Provider):
    name = "ikhokha"

    async def create_link(self, creds, *, amount_cents, reference, description,
                          success_url, cancel_url, notify_url, customer, mode):
        url = "https://api.ikhokha.com/public-api/v1/api/payment"
        payload = {
            "entityID": creds.get("entity_id", ""), "externalEntityID": "",
            "amount": int(amount_cents), "currency": "ZAR",
            "requesterUrl": success_url, "description": (description or "Invoice")[:50],
            "paymentReference": reference, "mode": "live" if mode != "test" else "test",
            "externalTransactionID": reference,
            "urls": {"callbackUrl": notify_url, "successPageUrl": success_url,
                     "failurePageUrl": cancel_url, "cancelUrl": cancel_url},
        }
        body_str = json.dumps(payload)
        sign = hmac.new(creds["app_secret"].encode(),
                        ("/public-api/v1/api/payment" + body_str).encode(), hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(url, content=body_str,
                headers={"IK-APPID": creds["app_id"], "IK-SIGN": sign, "Content-Type": "application/json"})
        r.raise_for_status()
        d = r.json()
        return PayLink(url=d.get("paylinkUrl") or d.get("paymentUrl"), provider=self.name, reference=reference, raw=d)

    async def verify_webhook(self, creds, headers, body, form):
        try:
            data = json.loads(body or b"{}") if body else (form or {})
        except Exception:
            data = form or {}
        status = str(data.get("status", data.get("paymentStatus", ""))).lower()
        paid = status in ("success", "complete", "paid", "successful")
        return {"reference": data.get("externalTransactionID") or data.get("paymentReference"), "paid": paid, "raw": data}


_REGISTRY = {p.name: p for p in (Yoco(), PayFast(), Peach(), IKhokha(), Ozow(), Paystack())}


def get_provider(name: str) -> Optional[_Provider]:
    return _REGISTRY.get(name)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _client():
    from vula.commerce import service as cs
    return cs._client()


# credentials is JSONB holding a different shape per provider (secret_key for yoco/paystack,
# merchant_id+merchant_key+passphrase for payfast, etc) — encrypted as one Fernet-wrapped JSON
# blob rather than field-by-field, stored inside the same JSONB column as {"_enc": "fernet:..."}
# so the column stays valid JSON. decrypt_creds() returns a dict UNCHANGED if it isn't wrapped
# this way, so rows written before this existed keep working until next saved.
def _encrypt_creds(creds: dict) -> dict:
    import json
    from vula.email_imap.credentials import encrypt_secret
    if not creds:
        return {}
    return {"_enc": encrypt_secret(json.dumps(creds))}


def _decrypt_creds(creds: Optional[dict]) -> dict:
    import json
    from vula.email_imap.credentials import decrypt_secret
    if not creds:
        return {}
    if "_enc" in creds:
        try:
            return json.loads(decrypt_secret(creds["_enc"]))
        except Exception:
            return {}
    return creds


def list_providers(tenant_id: str) -> list:
    try:
        rows = (_client().table("vula_payment_providers")
                .select("id,provider,mode,is_default,active,credentials")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        logger.debug("payment providers list skipped (run migration 039?): %s", exc)
        return []
    # Never leak secrets — report only which fields are set.
    for r in rows:
        creds = _decrypt_creds(r.pop("credentials", {}))
        r["connected_fields"] = [k for k, v in creds.items() if v]
    return rows


def default_provider_row(tenant_id: str) -> Optional[dict]:
    try:
        rows = (_client().table("vula_payment_providers").select("*")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    for r in rows:
        r["credentials"] = _decrypt_creds(r.get("credentials"))
    return next((r for r in rows if r.get("is_default")), rows[0])


def upsert_provider(tenant_id: str, provider: str, credentials: dict, mode: str = "live",
                    is_default: bool = False) -> dict:
    if provider not in _REGISTRY:
        raise ValueError(f"Unknown provider {provider}")
    import uuid
    from datetime import datetime, timezone
    db = _client()
    now = datetime.now(timezone.utc).isoformat()
    if is_default:  # clear other defaults first
        try:
            db.table("vula_payment_providers").update({"is_default": False}).eq("tenant_id", tenant_id).execute()
        except Exception:
            pass
    row = {"tenant_id": tenant_id, "provider": provider, "credentials": _encrypt_creds(credentials or {}),
           "mode": mode, "is_default": is_default, "active": True, "updated_at": now}
    existing = (db.table("vula_payment_providers").select("id")
                .eq("tenant_id", tenant_id).eq("provider", provider).limit(1).execute().data or [])
    if existing:
        db.table("vula_payment_providers").update(row).eq("id", existing[0]["id"]).execute()
        row["id"] = existing[0]["id"]
    else:
        row["id"] = str(uuid.uuid4())
        db.table("vula_payment_providers").insert(row).execute()
    row.pop("credentials", None)
    return row


def delete_provider(tenant_id: str, provider: str) -> None:
    _client().table("vula_payment_providers").delete() \
        .eq("tenant_id", tenant_id).eq("provider", provider).execute()


def set_default(tenant_id: str, provider: str) -> None:
    db = _client()
    db.table("vula_payment_providers").update({"is_default": False}).eq("tenant_id", tenant_id).execute()
    db.table("vula_payment_providers").update({"is_default": True}) \
        .eq("tenant_id", tenant_id).eq("provider", provider).execute()


async def create_pay_link(tenant_id: str, *, amount_cents: int, reference: str, description: str,
                          success_url: str, cancel_url: str, notify_url: str,
                          customer: dict = None) -> Optional[PayLink]:
    """Create a hosted pay link via the tenant's default gateway. Falls back to a connected
    Yoco account (legacy) if no provider is configured."""
    row = default_provider_row(tenant_id)
    if row:
        prov = get_provider(row["provider"])
        if prov:
            return await prov.create_link(row.get("credentials") or {}, amount_cents=amount_cents,
                reference=reference, description=description, success_url=success_url,
                cancel_url=cancel_url, notify_url=notify_url, customer=customer or {}, mode=row.get("mode", "live"))
    # Legacy fallback: a connected Yoco account in vula_yoco_accounts.
    try:
        from vula.api.yoco import _get_tenant_yoco_creds
        yc = await _get_tenant_yoco_creds(tenant_id)
        if yc and yc.get("secret_key"):
            return await Yoco().create_link({"secret_key": yc["secret_key"]}, amount_cents=amount_cents,
                reference=reference, description=description, success_url=success_url,
                cancel_url=cancel_url, notify_url=notify_url, customer=customer or {}, mode="live")
    except Exception:
        pass
    return None
