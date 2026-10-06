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
                .in_("status", ["open", "claimed", "abandoned", "needs_follow_up"]).execute().data or [])

    def get_bill(self, tenant_id: str, bill_id: str) -> Optional[dict]:
        return self._one(self.db.table("kb_bills").select("*").eq("tenant_id", tenant_id).eq("id", bill_id))

    def create_bill(self, row: dict) -> dict:
        return self.db.table("kb_bills").insert(row).execute().data[0]

    def cas_bill(self, tenant_id, bill_id, *, expected, new, fields=None) -> bool:
        rows = (self.db.table("kb_bills").update({**(fields or {}), "status": new, "updated_at": "now()"})
                .eq("tenant_id", tenant_id).eq("id", bill_id).eq("status", expected).execute().data or [])
        return bool(rows)

    def update_bill(self, tenant_id, bill_id, fields) -> None:
        self.db.table("kb_bills").update({**fields, "updated_at": "now()"}).eq("tenant_id", tenant_id).eq("id", bill_id).execute()

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

    # coach app
    def get_member(self, tenant_id: str, member_id: str) -> Optional[dict]:
        return self._one(self.db.table("vula_team_members").select("id,name,whatsapp,role,active")
                         .eq("tenant_id", tenant_id).eq("id", member_id))

    def put_enrol_code(self, tenant_id, member_id, code_hash, expires_at, created_by) -> None:
        self.db.table("kb_enrol_codes").delete().eq("tenant_id", tenant_id).eq("member_id", member_id).execute()
        self.db.table("kb_enrol_codes").insert({
            "tenant_id": tenant_id, "member_id": member_id, "code_hash": code_hash,
            "expires_at": expires_at.isoformat(), "created_by": created_by}).execute()

    def get_enrol_code(self, tenant_id, member_id) -> Optional[dict]:
        return self._one(self.db.table("kb_enrol_codes").select("*").eq("tenant_id", tenant_id)
                         .eq("member_id", member_id).order("created_at", desc=True))

    def bump_enrol_attempts(self, code_id: str) -> None:
        row = self._one(self.db.table("kb_enrol_codes").select("attempts").eq("id", code_id)) or {}
        self.db.table("kb_enrol_codes").update({"attempts": int(row.get("attempts", 0)) + 1}).eq("id", code_id).execute()

    def burn_enrol_code(self, code_id: str, now_iso: str) -> None:
        self.db.table("kb_enrol_codes").update({"used_at": now_iso}).eq("id", code_id).execute()

    def create_device(self, row: dict) -> dict:
        return self.db.table("kb_devices").insert(row).execute().data[0]

    def get_device(self, tenant_id, device_id) -> Optional[dict]:
        return self._one(self.db.table("kb_devices").select("*").eq("tenant_id", tenant_id).eq("id", device_id))

    def get_device_by_token_hash(self, token_hash: str) -> Optional[dict]:
        return self._one(self.db.table("kb_devices").select("*").eq("token_hash", token_hash))

    def update_device(self, tenant_id, device_id, fields) -> None:
        self.db.table("kb_devices").update(fields).eq("tenant_id", tenant_id).eq("id", device_id).execute()

    def list_devices(self, tenant_id: str) -> list[dict]:
        return (self.db.table("kb_devices").select("id,member_id,label,last_seen_at,revoked_at,created_at")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).execute().data or [])

    def upsert_push_sub(self, row: dict) -> None:
        self.db.table("kb_push_subs").upsert(row, on_conflict="endpoint").execute()

    def delete_push_sub(self, endpoint: str) -> None:
        self.db.table("kb_push_subs").delete().eq("endpoint", endpoint).execute()

    def push_subs_for(self, tenant_id, member_id) -> list[dict]:
        return (self.db.table("kb_push_subs").select("*").eq("tenant_id", tenant_id)
                .eq("member_id", member_id).execute().data or [])

    def bill_updates(self, tenant_id, since_iso, staff_id) -> list[dict]:
        q = (self.db.table("kb_bills").select("id,status,description,subtotal_cents,staff_id,is_test,updated_at")
             .eq("tenant_id", tenant_id).gt("updated_at", since_iso).order("updated_at"))
        if staff_id:
            q = q.eq("staff_id", staff_id)
        return q.limit(50).execute().data or []

    def paid_detail(self, tenant_id, bill_id, party_id) -> dict:
        """Tip and the given party's share for a paid bill (from the append-only ledger)."""
        s = self._one(self.db.table("kb_sessions").select("id,tip_cents,bill_cents").eq("tenant_id", tenant_id)
                      .eq("bill_id", bill_id).eq("state", "paid"))
        if not s:
            return {}
        out = {"tip_cents": int(s["tip_cents"]), "total_cents": int(s["tip_cents"]) + int(s["bill_cents"])}
        pay = self._one(self.db.table("kb_payments").select("id,created_at").eq("tenant_id", tenant_id).eq("session_id", s["id"]))
        if pay:
            out["paid_at"], out["ref"] = pay["created_at"], str(pay["id"])[:8].upper()
        if pay and party_id:
            lines = (self.db.table("kb_ledger_lines").select("cents").eq("tenant_id", tenant_id)
                     .eq("payment_id", pay["id"]).eq("party_id", party_id).execute().data or [])
            out["share_cents"] = sum(int(l["cents"]) for l in lines)
        return out

    # receipts
    def receipt_source(self, payment_id: str) -> Optional[dict]:
        pay = self._one(self.db.table("kb_payments")
                        .select("id,tenant_id,session_id,created_at,receipt_nonce,receipt_revoked_at").eq("id", payment_id))
        if not pay:
            return None
        s = self._one(self.db.table("kb_sessions").select("bill_id,bill_cents,tip_cents")
                      .eq("tenant_id", pay["tenant_id"]).eq("id", pay["session_id"])) or {}
        b = (self._one(self.db.table("kb_bills").select("description,staff_id,is_test")
                       .eq("tenant_id", pay["tenant_id"]).eq("id", s["bill_id"])) if s.get("bill_id") else None) or {}
        return {"payment_id": pay["id"], "tenant_id": pay["tenant_id"], "paid_at": pay["created_at"],
                "nonce": int(pay.get("receipt_nonce") or 0), "revoked_at": pay.get("receipt_revoked_at"),
                "bill_cents": int(s.get("bill_cents") or 0), "tip_cents": int(s.get("tip_cents") or 0),
                "description": b.get("description"), "staff_id": b.get("staff_id"), "is_test": bool(b.get("is_test"))}

    def revoke_receipt(self, tenant_id: str, payment_id: str) -> None:
        self.db.table("kb_payments").update({"receipt_revoked_at": "now()"}) \
            .eq("tenant_id", tenant_id).eq("id", payment_id).execute()

    def merchant_vat(self, tenant_id: str) -> dict:
        try:
            r = self._one(self.db.table("commerce_invoice_settings").select("vat_number,vat_registered")
                          .eq("tenant_id", tenant_id)) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("merchant_vat lookup failed: %s", exc)
            r = {}
        return {"vat_number": r.get("vat_number") or None, "vat_registered": bool(r.get("vat_registered", False))}

    # tax invoices
    def invoice_settings(self, tenant_id: str) -> Optional[dict]:
        return self._one(self.db.table("commerce_invoice_settings").select("*").eq("tenant_id", tenant_id))

    def get_tax_invoice(self, tenant_id: str, payment_id: str) -> Optional[dict]:
        return self._one(self.db.table("kb_tax_invoices").select("*")
                         .eq("tenant_id", tenant_id).eq("payment_id", payment_id))

    def insert_tax_invoice(self, row: dict) -> Optional[dict]:
        """None when another request won the race (same payment or same number)."""
        try:
            return self.db.table("kb_tax_invoices").insert(row).execute().data[0]
        except Exception as exc:  # noqa: BLE001
            if _is_dup(exc):
                return None
            raise

    def max_tax_number(self, tenant_id: str) -> int:
        r = self._one(self.db.table("kb_tax_invoices").select("number").eq("tenant_id", tenant_id)
                      .order("number", desc=True))
        return int((r or {}).get("number") or 0)

    def latest_paid_payment(self, tenant_id: str, payer_hash: str) -> Optional[str]:
        """The customer's most recent real (non-test) payment, as a payment id."""
        for s in (self.db.table("kb_sessions").select("id,bill_id").eq("tenant_id", tenant_id)
                  .eq("payer_hash", payer_hash).eq("state", "paid")
                  .order("created_at", desc=True).limit(5).execute().data or []):
            b = (self._one(self.db.table("kb_bills").select("is_test").eq("tenant_id", tenant_id)
                           .eq("id", s["bill_id"])) if s.get("bill_id") else None) or {}
            if b.get("is_test"):
                continue
            pay = self._one(self.db.table("kb_payments").select("id").eq("tenant_id", tenant_id)
                            .eq("session_id", s["id"]))
            if pay:
                return pay["id"]
        return None

    def put_tax_request(self, row: dict) -> None:
        self.db.table("kb_tax_requests").update({"status": "cancelled"}).eq("tenant_id", row["tenant_id"]) \
            .eq("payer_hash", row["payer_hash"]).eq("status", "awaiting").execute()
        self.db.table("kb_tax_requests").insert(row).execute()

    def open_tax_request(self, tenant_id: str, payer_hash: str, now_iso: str) -> Optional[dict]:
        return self._one(self.db.table("kb_tax_requests").select("*").eq("tenant_id", tenant_id)
                         .eq("payer_hash", payer_hash).eq("status", "awaiting").gt("expires_at", now_iso)
                         .order("created_at", desc=True))

    def close_tax_request(self, request_id: str, status: str) -> None:
        self.db.table("kb_tax_requests").update({"status": status}).eq("id", request_id).execute()

    # unpaid-bill reminders
    def stale_sessions(self, now_iso: str, limit: int = 100) -> list[dict]:
        return (self.db.table("kb_sessions").select("*").in_("state", _LIVE_SESSION)
                .lt("expires_at", now_iso).limit(limit).execute().data or [])

    def enabled_tenants(self) -> list[str]:
        rows = self.db.table("kb_settings").select("tenant_id").in_("mode", ["testing", "live"]).execute().data or []
        return [r["tenant_id"] for r in rows]

    def bills_with_status(self, tenant_id: str, status: str, limit: int = 100) -> list[dict]:
        return (self.db.table("kb_bills").select("*").eq("tenant_id", tenant_id).eq("status", status)
                .order("abandoned_at").limit(limit).execute().data or [])

    def unpaid_bills(self, tenant_id: str) -> list[dict]:
        return (self.db.table("kb_bills").select("*").eq("tenant_id", tenant_id)
                .in_("status", ["abandoned", "needs_follow_up"]).order("abandoned_at", desc=True)
                .limit(50).execute().data or [])

    def reminders_for_bill(self, tenant_id: str, bill_id: str) -> list[dict]:
        return (self.db.table("kb_reminders").select("*").eq("tenant_id", tenant_id).eq("bill_id", bill_id)
                .order("sent_at").execute().data or [])

    def insert_reminder(self, row: dict) -> Optional[dict]:
        try:
            return self.db.table("kb_reminders").insert(row).execute().data[0]
        except Exception as exc:  # noqa: BLE001
            if _is_dup(exc):                       # another worker already claimed this reminder number
                return None
            raise

    def update_reminder(self, reminder_id: str, fields: dict) -> None:
        self.db.table("kb_reminders").update(fields).eq("id", reminder_id).execute()

    def delete_reminder(self, reminder_id: str) -> None:
        self.db.table("kb_reminders").delete().eq("id", reminder_id).execute()

    def cancel_other_sessions(self, tenant_id: str, bill_id: str, keep: Optional[str]) -> None:
        q = (self.db.table("kb_sessions").update({"state": "cancelled", "updated_at": "now()"})
             .eq("tenant_id", tenant_id).eq("bill_id", bill_id).in_("state", _LIVE_SESSION))
        if keep:
            q = q.neq("id", keep)
        q.execute()

    def is_opted_out(self, tenant_id: str, phone: str) -> bool:
        try:
            from vula.api.commerce import _norm_phone
            r = self._one(self.db.table("commerce_consent").select("status")
                          .eq("tenant_id", tenant_id).eq("phone", _norm_phone(phone)))
            return bool(r and r.get("status") == "opted_out")
        except Exception as exc:  # noqa: BLE001 — if we can't tell, don't message
            logger.warning("opt-out lookup failed, treating as opted out: %s", exc)
            return True

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
