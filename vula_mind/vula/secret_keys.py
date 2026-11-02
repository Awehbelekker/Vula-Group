"""vula/secret_keys.py — the key that locks every saved login and API key, kept apart from the database key.

2 Nov 2026. Mailbox passwords, payment keys, ClickUp/Google/Microsoft tokens and the Tap to Pay
payer numbers are stored encrypted ("fernet:..." via vula/email_imap/credentials.py). Until now the
key was made from the Supabase service key, so when that key was changed (22 Oct) every login
saved before then quietly unlocked to "" — both connected mailboxes then failed to sign in from
23 Oct and the dashboard still said "connected".

Now:
- SECRETS_KEY (Railway, set by Ian) is the key everything new is saved with. Unset = the old key,
  so nothing changes until it is set.
- Older keys still unlock what they saved: SECRETS_PREVIOUS_KEYS, then the old Supabase-made key.
- At boot (scheduler process), every saved value that the newest key can't unlock but an older one
  can is re-saved under the newest key and read back (`reencrypt_all`). Compare-and-set per value,
  so two workers doing it at once is harmless.
- A value no key can unlock is logged loudly once, and `undecryptable()` lists them per business
  for /ready, with where the owner re-enters it. Callers still get "" as before.
- No value and no key ever reaches a log or a reply: logs carry a short fingerprint of the stored
  ciphertext only.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PREFIX = "fernet:"
_LEGACY_FALLBACK_SEED = "vula-email"


def _derive(seed: str):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()))


def _seeds() -> Tuple[str, ...]:
    """Newest first: SECRETS_KEY, SECRETS_PREVIOUS_KEYS, the old Supabase-made key."""
    from config import settings
    out: List[str] = []
    if (settings.secrets_key or "").strip():
        out.append(settings.secrets_key.strip())
    out += [s.strip() for s in (settings.secrets_previous_keys or "").split(",") if s.strip()]
    out.append(settings.supabase_service_role_key or settings.supabase_service_key or _LEGACY_FALLBACK_SEED)
    seen: List[str] = []
    for s in out:
        if s not in seen:
            seen.append(s)
    return tuple(seen)


@lru_cache(maxsize=4)
def _ring(seeds: Tuple[str, ...]):
    return [_derive(s) for s in seeds]


def keys():
    """The Fernet keys, newest first. Cached per set of seeds, so a settings change in tests applies."""
    return _ring(_seeds())


def own_key_set() -> bool:
    from config import settings
    return bool((settings.secrets_key or "").strip())


def fingerprint(stored: str) -> str:
    return hashlib.sha256((stored or "").encode()).hexdigest()[:10]


_WARNED: set = set()


def encrypt(plain: str) -> str:
    return PREFIX + keys()[0].encrypt(plain.encode()).decode()


def decrypt(stored: str) -> Optional[str]:
    """The plaintext of a "fernet:" value, or None when no key unlocks it (logged once per value)."""
    token = stored[len(PREFIX):].encode()
    for f in keys():
        try:
            return f.decrypt(token).decode()
        except Exception:  # noqa: BLE001 — InvalidToken, or a malformed value
            continue
    fp = fingerprint(stored)
    if fp not in _WARNED:
        _WARNED.add(fp)
        logger.error("saved secret can't be unlocked by any key (fingerprint %s): the encryption key "
                     "changed since it was saved — it has to be entered again (see GET /ready)", fp)
    return None


def _newest_unlocks(stored: str) -> bool:
    try:
        keys()[0].decrypt(stored[len(PREFIX):].encode())
        return True
    except Exception:  # noqa: BLE001
        return False


# ── where secrets are stored ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Field:
    table: str
    key_col: str          # column that identifies the row (id, or tenant_id where there is no id)
    col: str
    what: str             # for the owner: "email password"
    fix: str              # where the owner enters it again; "" = not something an owner re-enters
    label_col: str = ""   # e.g. the mailbox address, shown next to `what`
    json_wrapped: bool = False   # {"_enc": "fernet:..."} inside a jsonb column
    many_rows: bool = False      # page through it (Tap to Pay bills/sessions)


FIELDS: Tuple[Field, ...] = (
    Field("vula_email_accounts", "id", "secret", "email password",
          "Settings › Email: connect the mailbox again with its password", label_col="email"),
    Field("vula_email_accounts", "id", "statement_password", "bank statement password",
          "Send the statement password again on WhatsApp", label_col="email"),
    Field("vula_whatsapp_accounts", "id", "access_token", "WhatsApp token",
          "Settings › WhatsApp: connect the number again"),
    Field("vula_payment_providers", "id", "credentials", "payment keys",
          "Settings › Payments: enter the keys again", label_col="provider", json_wrapped=True),
    Field("vula_yoco_accounts", "id", "secret_key", "Yoco secret key", "Settings › Payments › Yoco: enter the keys again"),
    Field("vula_yoco_accounts", "id", "webhook_secret", "Yoco webhook secret", "Settings › Payments › Yoco: connect again"),
    Field("vula_clickup_accounts", "id", "api_token", "ClickUp token", "Settings › ClickUp: enter the token again"),
    Field("vula_clickup_accounts", "id", "webhook_secret", "ClickUp webhook secret", "Settings › ClickUp: connect again"),
    Field("vula_google_accounts", "id", "refresh_token", "Google sign-in", "Settings › Google: connect again", label_col="email"),
    Field("vula_google_accounts", "id", "access_token", "Google sign-in", "", label_col="email"),
    Field("vula_microsoft_accounts", "id", "refresh_token", "Microsoft sign-in", "Settings › Microsoft: connect again",
          label_col="email"),
    Field("vula_microsoft_accounts", "id", "access_token", "Microsoft sign-in", "", label_col="email"),
    Field("vula_dynamics365_accounts", "id", "refresh_token", "Dynamics 365 sign-in", "Settings › Dynamics 365: connect again",
          label_col="email"),
    Field("vula_dynamics365_accounts", "id", "access_token", "Dynamics 365 sign-in", "", label_col="email"),
    Field("vula_shopify_accounts", "tenant_id", "access_token", "Shopify token", "Settings › Shopify: connect again"),
    Field("vula_woocommerce_accounts", "tenant_id", "consumer_key", "WooCommerce keys", "Settings › WooCommerce: connect again"),
    Field("vula_woocommerce_accounts", "tenant_id", "consumer_secret", "WooCommerce keys", "Settings › WooCommerce: connect again"),
    Field("vula_woocommerce_accounts", "tenant_id", "webhook_secret", "WooCommerce webhook secret", ""),
    Field("kb_bills", "id", "customer_enc", "Tap to Pay customer number", "", many_rows=True),
    Field("kb_sessions", "id", "payer_enc", "Tap to Pay payer number", "", many_rows=True),
)

_PAGE = 500


def _stored(f: Field, row: Dict[str, Any]) -> Optional[str]:
    v = row.get(f.col)
    if f.json_wrapped:
        v = v.get("_enc") if isinstance(v, dict) else None
    return v if isinstance(v, str) and v.startswith(PREFIX) else None


def _rows(db, f: Field) -> List[Dict[str, Any]]:
    cols = ",".join(dict.fromkeys(c for c in (f.key_col, "tenant_id", f.col, f.label_col) if c))
    if not f.many_rows:
        return db.table(f.table).select(cols).execute().data or []
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        page = (db.table(f.table).select(cols).like(f.col, PREFIX + "%")
                .order(f.key_col).range(start, start + _PAGE - 1).execute().data or [])
        out += page
        if len(page) < _PAGE:
            return out
        start += _PAGE


def _each(db, on_table_error: Callable[[Field, Exception], None], owner_fixable_only: bool = False):
    for f in FIELDS:
        if owner_fixable_only and not f.fix:
            continue
        try:
            rows = _rows(db, f)
        except Exception as exc:  # noqa: BLE001 — a table this database doesn't have
            on_table_error(f, exc)
            continue
        for r in rows:
            stored = _stored(f, r)
            if stored:
                yield f, r, stored


def undecryptable(db) -> Dict[str, List[Dict[str, str]]]:
    """Saved values no key unlocks, per business: [{what, fix}], one entry per thing to re-enter."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for f, r, stored in _each(db, lambda f, exc: logger.debug("secret check skipped %s: %s", f.table, exc),
                              owner_fixable_only=True):
        if decrypt(stored) is not None:
            continue
        label = r.get(f.label_col) if f.label_col else ""
        what = f"Saved {f.what}{f' ({label})' if label else ''} can't be unlocked: it was saved before " \
               f"the encryption key changed, so it isn't being used"
        gaps = out.setdefault(r.get("tenant_id") or "?", [])
        if not any(g["what"] == what for g in gaps):
            gaps.append({"what": what, "fix": f.fix})
    return out


def reencrypt_all(db) -> Dict[str, Any]:
    """Re-save, under the newest key, every value an older key saved. Each write is compare-and-set
    on the old value and read back; a value no key unlocks is left alone and counted."""
    counts = {"resaved": 0, "current": 0, "locked": 0, "failed": 0}
    if len(keys()) < 2:
        return counts
    for f, r, stored in _each(db, lambda f, exc: logger.debug("re-encrypt skipped %s: %s", f.table, exc)):
        if _newest_unlocks(stored):
            counts["current"] += 1
            continue
        plain = decrypt(stored)
        if plain is None:
            counts["locked"] += 1
            continue
        new = encrypt(plain)
        new_val: Any = {**r[f.col], "_enc": new} if f.json_wrapped else new
        try:
            q = db.table(f.table).update({f.col: new_val}).eq(f.key_col, r[f.key_col])
            q = q.eq(f"{f.col}->>_enc", stored) if f.json_wrapped else q.eq(f.col, stored)
            q.execute()
            back = (db.table(f.table).select(f.col).eq(f.key_col, r[f.key_col]).limit(1).execute().data or [{}])[0]
            got = _stored(f, back)
            if got and keys()[0].decrypt(got[len(PREFIX):].encode()).decode() == plain:
                counts["resaved"] += 1
            else:
                counts["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            logger.warning("re-encrypt of %s.%s failed (%s)", f.table, f.col, type(exc).__name__)
    if counts["resaved"] or counts["locked"] or counts["failed"]:
        logger.info("saved secrets re-saved under the newest key: %s", counts)
    return counts
