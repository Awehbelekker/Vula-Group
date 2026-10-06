"""Supabase implementation of `ports.Repo`. Thin by design: every compare-and-set is ONE
conditional UPDATE (`... WHERE status = expected`), which is what makes claims and state changes
atomic across workers — never read-then-write.

NOTE: exercised in unit tests only through the in-memory fake; the SQL shape was validated against
a real Postgres (migrations 199/200) but this client code needs a staging run (docs/staging.md)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

_LIVE_SESSION = ["claimed", "awaiting_amount", "awaiting_tip", "awaiting_confirm", "awaiting_payment"]


def _is_dup(exc: Exception) -> bool:
    s = str(exc).lower()
    return "23505" in s or "duplicate key" in s or "already exists" in s


class SupabaseRepo:
    def __init__(self, client=None):
        self._c = client

    @property
    def db(self):
        if self._c is None:
            from vula.commerce import service as cs
            self._c = cs._client()
        return self._c

    def _one(self, q) -> Optional[dict]:
        rows = q.limit(1).execute().data or []
        return rows[0] if rows else None

    # tags / tokens
    def get_tag_by_code(self, code: str) -> Optional[dict]:
        return self._one(self.db.table("kb_tags").select("*").eq("code", code))

    def get_tag(self, tenant_id: str, tag_id: str) -> Optional[dict]:
        return self._one(self.db.table("kb_tags").select("*").eq("tenant_id", tenant_id).eq("id", tag_id))

    def create_claim_token(self, *, tenant_id, tag_id, token_hash, expires_at: datetime) -> None:
        self.db.table("kb_claim_tokens").insert({
            "tenant_id": tenant_id, "tag_id": tag_id, "token_hash": token_hash,
            "expires_at": expires_at.isoformat()}).execute()

    def consume_claim_token(self, token_hash: str, now: datetime) -> Optional[dict]:
        rows = (self.db.table("kb_claim_tokens").update({"used_at": now.isoformat()})
                .eq("token_hash", token_hash).is_("used_at", "null")
                .gt("expires_at", now.isoformat()).execute().data or [])
        return rows[0] if rows else None

    # bills
    def live_bills_for_tag(self, tenant_id: str, tag_id: str) -> list[dict]:
        return (self.db.table("kb_bills").select("*").eq("tenant_id", tenant_id).eq("tag_id", tag_id)
                .in_("status", ["open", "claimed"]).execute().data or [])

    def get_bill(self, tenant_id: str, bill_id: str) -> Optional[dict]:
        return self._one(self.db.table("kb_bills").select("*").eq("tenant_id", tenant_id).eq("id", bill_id))

    def create_bill(self, row: dict) -> dict:
        return self.db.table("kb_bills").insert(row).execute().data[0]

    def cas_bill(self, tenant_id, bill_id, *, expected, new, fields=None) -> bool:
        rows = (self.db.table("kb_bills").update({**(fields or {}), "status": new})
                .eq("tenant_id", tenant_id).eq("id", bill_id).eq("status", expected).execute().data or [])
        return bool(rows)

    def update_bill(self, tenant_id, bill_id, fields) -> None:
        self.db.table("kb_bills").update(fields).eq("tenant_id", tenant_id).eq("id", bill_id).execute()

    # sessions
    def create_session(self, row: dict) -> dict:
        return self.db.table("kb_sessions").insert(row).execute().data[0]

    def get_session(self, tenant_id, session_id) -> Optional[dict]:
        return self._one(self.db.table("kb_sessions").select("*")
                         .eq("tenant_id", tenant_id).eq("id", session_id))

    def get_session_any_tenant(self, session_id) -> Optional[dict]:
        return self._one(self.db.table("kb_sessions").select("*").eq("id", session_id))

    def active_session_for_payer(self, tenant_id, payer_hash, now) -> Optional[dict]:
        return self._one(self.db.table("kb_sessions").select("*").eq("tenant_id", tenant_id)
                         .eq("payer_hash", payer_hash).in_("state", _LIVE_SESSION)
                         .order("created_at", desc=True))

    def cas_session(self, tenant_id, session_id, *, expected, new, fields=None) -> bool:
        rows = (self.db.table("kb_sessions").update({**(fields or {}), "state": new, "updated_at": "now()"})
                .eq("tenant_id", tenant_id).eq("id", session_id).eq("state", expected).execute().data or [])
        return bool(rows)

    # money
    def record_event(self, *, tenant_id, source, event_id) -> bool:
        try:
            self.db.table("kb_webhook_events").insert(
                {"tenant_id": tenant_id, "source": source, "event_id": event_id}).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_dup(exc):
                return False
            raise

    def record_payment(self, row: dict) -> Optional[dict]:
        try:
            return self.db.table("kb_payments").insert(row).execute().data[0]
        except Exception as exc:  # noqa: BLE001
            if _is_dup(exc):
                return None
            raise

    def insert_ledger_lines(self, rows: Sequence[dict]) -> None:
        if rows:
            self.db.table("kb_ledger_lines").insert(list(rows)).execute()

    def get_split_rule(self, tenant_id, staff_id) -> Optional[dict]:
        if staff_id:
            r = self._one(self.db.table("kb_split_rules").select("*")
                          .eq("tenant_id", tenant_id).eq("scope", f"staff:{staff_id}"))
            if r:
                return r
        return self._one(self.db.table("kb_split_rules").select("*")
                         .eq("tenant_id", tenant_id).eq("scope", "default"))

    # setup / admin
    def get_settings(self, tenant_id: str) -> Optional[dict]:
        return self._one(self.db.table("kb_settings").select("*").eq("tenant_id", tenant_id))

    def upsert_settings(self, tenant_id: str, fields: dict) -> None:
        self.db.table("kb_settings").upsert(
            {"tenant_id": tenant_id, **fields, "updated_at": "now()"}, on_conflict="tenant_id").execute()

    def list_tags(self, tenant_id: str) -> list[dict]:
        return (self.db.table("kb_tags").select("*").eq("tenant_id", tenant_id)
                .order("created_at").execute().data or [])

    def create_tag(self, row: dict) -> dict:
        return self.db.table("kb_tags").insert(row).execute().data[0]

    def update_tag(self, tenant_id: str, tag_id: str, fields: dict) -> None:
        self.db.table("kb_tags").update(fields).eq("tenant_id", tenant_id).eq("id", tag_id).execute()

    def recent_bills(self, tenant_id: str, limit: int = 20) -> list[dict]:
        return (self.db.table("kb_bills")
                .select("id,tag_id,status,description,subtotal_cents,staff_id,is_test,created_at")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute().data or [])

    def list_split_rules(self, tenant_id: str) -> list[dict]:
        return self.db.table("kb_split_rules").select("*").eq("tenant_id", tenant_id).execute().data or []

    def upsert_split_rule(self, tenant_id: str, scope: str, fields: dict) -> None:
        self.db.table("kb_split_rules").upsert(
            {"tenant_id": tenant_id, "scope": scope, **fields}, on_conflict="tenant_id,scope").execute()

    def team_members(self, tenant_id: str) -> list[dict]:
        return (self.db.table("vula_team_members").select("id,name,whatsapp,role,active")
                .eq("tenant_id", tenant_id).eq("active", True).order("created_at").execute().data or [])

    # display
    def merchant_name(self, tenant_id: str) -> str:
        try:
            r = self._one(self.db.table("vula_tenants").select("company_name").eq("tenant_id", tenant_id))
            if r and r.get("company_name"):
                return r["company_name"]
        except Exception as exc:  # noqa: BLE001
            logger.debug("merchant_name lookup failed: %s", exc)
        return tenant_id

    def staff_name(self, tenant_id, staff_id) -> Optional[str]:
        if not staff_id:
            return None
        r = self._one(self.db.table("vula_team_members").select("name")
                      .eq("tenant_id", tenant_id).eq("id", staff_id))
        name = (r or {}).get("name") or ""
        return name.split(" ")[0] or None

    def tenant_wa_number(self, tenant_id: str) -> Optional[str]:
        r = self._one(self.db.table("vula_whatsapp_accounts").select("phone_number")
                      .eq("tenant_id", tenant_id).eq("status", "connected"))
        return (r or {}).get("phone_number")

    def team_phones(self, tenant_id, staff_id) -> list[str]:
        """The serving staff member if known, else owners/managers."""
        q = self.db.table("vula_team_members").select("whatsapp,role").eq("tenant_id", tenant_id).eq("active", True)
        if staff_id:
            q = q.eq("id", staff_id)
        else:
            q = q.in_("role", ["owner", "manager"])
        return [r["whatsapp"] for r in (q.execute().data or []) if r.get("whatsapp")]
