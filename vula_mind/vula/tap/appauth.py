"""Sign-in for the coach app: enrolment code -> device + PIN -> short-lived signed access token.

Why not Supabase auth: coaches and cashiers have a phone and a WhatsApp number, not an email login.
Threat model handled here:
  * enrolment code: 6 digits, 10 minutes, bound to ONE team member (the caller must also know that
    member's WhatsApp number), 5 wrong tries burns it, issuing a new one voids the old one;
  * device token: 256-bit random, stored only as SHA-256; useless without the PIN;
  * PIN: PBKDF2-SHA256 with a per-device salt; 5 wrong tries lock the device for 15 minutes (the
    PIN's small keyspace makes this lockout, not the hash, the real defence);
  * access token: HMAC-SHA256 signed {tenant, member, device, exp}; every request re-checks that the
    device isn't revoked and the member is still active, so revoking works at once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from vula.tap.ports import Repo

CODE_TTL = timedelta(minutes=10)
MAX_CODE_TRIES = 5
MAX_PIN_TRIES = 5
PIN_LOCK = timedelta(minutes=15)
ACCESS_TTL = timedelta(hours=12)
PBKDF2_ROUNDS = 200_000
PIN_RE = re.compile(r"^\d{4,6}$")
MANAGER_ROLES = ("owner", "manager")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message, self.status = message, status


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), PBKDF2_ROUNDS).hex()


def sign_token(secret: str, payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(secret: str, token: str, now: datetime) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        p = json.loads(_unb64(body))
        return p if p.get("exp", 0) > now.timestamp() else None
    except Exception:  # noqa: BLE001 — any malformed token is simply invalid
        return None


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Actor:
    tenant_id: str
    member_id: str
    device_id: str
    role: str
    name: str

    @property
    def sees_all(self) -> bool:
        return self.role in MANAGER_ROLES


@dataclass
class AppAuth:
    repo: Repo
    secret: str
    clock: Callable[[], datetime] = _utcnow

    # ── owner side ───────────────────────────────────────────────────────────────────────────
    def issue_enrol_code(self, tenant: str, member_id: str, issued_by: str) -> dict:
        member = self.repo.get_member(tenant, member_id)
        if not member or not member.get("active", True):
            raise AuthError("Unknown team member.", 404)
        if not _digits(member.get("whatsapp") or ""):
            raise AuthError("Add this person's WhatsApp number under Team first.", 422)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.repo.put_enrol_code(tenant, member_id, _sha(f"{tenant}:{member_id}:{code}"),
                                 self.clock() + CODE_TTL, issued_by)
        return {"code": code, "expires_in_minutes": int(CODE_TTL.total_seconds() // 60), "name": member.get("name")}

    def revoke_device(self, tenant: str, device_id: str) -> None:
        self.repo.update_device(tenant, device_id, {"revoked_at": self.clock().isoformat()})

    # ── coach side ───────────────────────────────────────────────────────────────────────────
    def enrol(self, tenant: str, phone: str, code: str, pin: str, label: str = "") -> dict:
        if not PIN_RE.match(pin or ""):
            raise AuthError("Choose a PIN of 4 to 6 digits.", 422)
        bad = AuthError("That code isn't right, or it has expired. Ask your manager for a new one.", 403)
        member = next((m for m in self.repo.team_members(tenant)
                       if _digits(m.get("whatsapp") or "") and _digits(m["whatsapp"]) == _digits(phone)), None)
        if not member:
            raise bad
        row = self.repo.get_enrol_code(tenant, member["id"])
        now = self.clock()
        if not row or row.get("used_at") or _ts(row["expires_at"]) <= now or int(row.get("attempts", 0)) >= MAX_CODE_TRIES:
            raise bad
        if not hmac.compare_digest(row["code_hash"], _sha(f"{tenant}:{member['id']}:{(code or '').strip()}")):
            self.repo.bump_enrol_attempts(row["id"])
            raise bad
        self.repo.burn_enrol_code(row["id"], now.isoformat())
        token = secrets.token_urlsafe(32)
        salt = secrets.token_hex(8)
        dev = self.repo.create_device({
            "tenant_id": tenant, "member_id": member["id"], "label": (label or "")[:60],
            "token_hash": _sha(token), "pin_salt": salt, "pin_hash": hash_pin(pin, salt)})
        return {"device_token": token, **self._access(tenant, member, dev["id"])}

    def login(self, device_token: str, pin: str) -> dict:
        dev = self.repo.get_device_by_token_hash(_sha(device_token or ""))
        if not dev or dev.get("revoked_at"):
            raise AuthError("This phone is no longer signed in. Ask your manager for a new code.", 401)
        now = self.clock()
        if dev.get("locked_until") and _ts(dev["locked_until"]) > now:
            raise AuthError("Too many wrong PINs. Try again in a few minutes.", 423)
        if not hmac.compare_digest(dev["pin_hash"], hash_pin(pin or "", dev["pin_salt"])):
            fails = int(dev.get("failed_pins", 0)) + 1
            lock = (now + PIN_LOCK).isoformat() if fails >= MAX_PIN_TRIES else None
            self.repo.update_device(dev["tenant_id"], dev["id"],
                                    {"failed_pins": 0 if lock else fails, "locked_until": lock})
            raise AuthError("Wrong PIN." if not lock else "Too many wrong PINs. Locked for 15 minutes.",
                            401 if not lock else 423)
        member = self.repo.get_member(dev["tenant_id"], dev["member_id"])
        if not member or not member.get("active", True):
            raise AuthError("This account has been switched off.", 403)
        self.repo.update_device(dev["tenant_id"], dev["id"],
                                {"failed_pins": 0, "locked_until": None, "last_seen_at": now.isoformat()})
        return self._access(dev["tenant_id"], member, dev["id"])

    def _access(self, tenant: str, member: dict, device_id: str) -> dict:
        exp = self.clock() + ACCESS_TTL
        tok = sign_token(self.secret, {"t": tenant, "m": member["id"], "d": device_id, "exp": exp.timestamp()})
        return {"access_token": tok, "expires_at": exp.isoformat(),
                "member": {"id": member["id"], "name": member.get("name"), "role": member.get("role", "staff")}}

    # ── per-request ──────────────────────────────────────────────────────────────────────────
    def actor(self, token: str) -> Actor:
        p = verify_token(self.secret, token or "", self.clock())
        if not p:
            raise AuthError("Please sign in again.", 401)
        dev = self.repo.get_device(p["t"], p["d"])
        if not dev or dev.get("revoked_at"):
            raise AuthError("This phone is no longer signed in.", 401)
        member = self.repo.get_member(p["t"], p["m"])
        if not member or not member.get("active", True):
            raise AuthError("This account has been switched off.", 403)
        return Actor(p["t"], member["id"], dev["id"], member.get("role") or "staff", member.get("name") or "")
