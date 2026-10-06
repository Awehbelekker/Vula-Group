"""In-memory fakes for the tap service ports (no Supabase, Meta or PayFast)."""
from __future__ import annotations

import itertools
from datetime import datetime, timezone


def _ts(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


class MemoryRepo:
    def __init__(self, clock):
        self.clock = clock
        self.n = itertools.count(1)
        self.tags, self.tokens, self.bills, self.sessions = {}, {}, {}, {}
        self.events, self.payments, self.ledger = set(), {}, []
        self.rules, self.wa = {}, {}
        self.names = {}
        self.team = {}
        self.settings = {}
        self.vat = {}
        self.codes, self.devices, self.subs = [], {}, {}
        self.reminders, self.opted_out = [], set()
        self.members = {}

    def _id(self):
        return f"id{next(self.n):04d}" + "x" * 4

    # tags / tokens
    def add_tag(self, tenant, code, **kw):
        t = {"id": self._id(), "tenant_id": tenant, "code": code, "mode": "appointment",
             "status": "active", "bound_id": None, "allow_open_amount": False, **kw}
        self.tags[t["id"]] = t
        return t

    def get_tag_by_code(self, code):
        return next((t for t in self.tags.values() if t["code"] == code), None)

    def get_tag(self, tenant_id, tag_id):
        t = self.tags.get(tag_id)
        return t if t and t["tenant_id"] == tenant_id else None

    def create_claim_token(self, *, tenant_id, tag_id, token_hash, expires_at):
        self.tokens[token_hash] = {"tenant_id": tenant_id, "tag_id": tag_id, "expires_at": expires_at, "used": False}

    def consume_claim_token(self, token_hash, now):
        t = self.tokens.get(token_hash)
        if not t or t["used"] or _ts(t["expires_at"]) <= now:
            return None
        t["used"] = True
        return dict(t)

    # bills
    def live_bills_for_tag(self, tenant_id, tag_id):
        return [dict(b) for b in self.bills.values() if b["tenant_id"] == tenant_id
                and b["tag_id"] == tag_id and b["status"] in ("open", "claimed", "abandoned", "needs_follow_up")]

    def get_bill(self, tenant_id, bill_id):
        b = self.bills.get(bill_id)
        return dict(b) if b and b["tenant_id"] == tenant_id else None

    def create_bill(self, row):
        b = {"id": self._id(), "code_attempts": 0, "code_locked_until": None, "customer_hash": None,
             "claimed_by_hash": None, "bill_code": None, "updated_at": self.clock().isoformat(), **row}
        self.bills[b["id"]] = b
        return dict(b)

    def cas_bill(self, tenant_id, bill_id, *, expected, new, fields=None):
        b = self.bills.get(bill_id)
        if not b or b["tenant_id"] != tenant_id or b["status"] != expected:
            return False
        b.update(fields or {})
        b["status"] = new
        b["updated_at"] = self.clock().isoformat()
        return True

    def update_bill(self, tenant_id, bill_id, fields):
        self.bills[bill_id].update(fields)
        self.bills[bill_id]["updated_at"] = self.clock().isoformat()

    # sessions
    def create_session(self, row):
        s = {"id": self._id(), "pay_nonce_hash": None, "expecting": None, "staff_ref": None,
             "checkout_ref": None, "created_at": self.clock().isoformat(), "updated_at": self.clock().isoformat(), **row}
        self.sessions[s["id"]] = s
        return dict(s)

    def get_session(self, tenant_id, session_id):
        s = self.sessions.get(session_id)
        return dict(s) if s and s["tenant_id"] == tenant_id else None

    def get_session_any_tenant(self, session_id):
        s = self.sessions.get(session_id)
        return dict(s) if s else None

    def active_session_for_payer(self, tenant_id, payer_hash, now):
        live = [s for s in self.sessions.values() if s["tenant_id"] == tenant_id
                and s["payer_hash"] == payer_hash
                and s["state"] in ("claimed", "awaiting_amount", "awaiting_tip", "awaiting_confirm", "awaiting_payment")]
        return dict(live[-1]) if live else None

    def cas_session(self, tenant_id, session_id, *, expected, new, fields=None):
        s = self.sessions.get(session_id)
        if not s or s["tenant_id"] != tenant_id or s["state"] != expected:
            return False
        s.update(fields or {})
        s["state"] = new
        s["updated_at"] = self.clock().isoformat()
        return True

    # money
    def record_event(self, *, tenant_id, source, event_id):
        k = (source, event_id)
        if k in self.events:
            return False
        self.events.add(k)
        return True

    def record_payment(self, row):
        k = (row["provider"], row["provider_ref"])
        if k in self.payments:
            return None
        p = {"id": self._id(), "receipt_nonce": 0, "receipt_revoked_at": None, **row}
        self.payments[k] = p
        return dict(p)

    def insert_ledger_lines(self, rows):
        self.ledger.extend(rows)

    def get_split_rule(self, tenant_id, staff_id):
        return self.rules.get((tenant_id, staff_id)) or self.rules.get((tenant_id, None))

    # setup / admin
    def get_settings(self, tenant_id):
        return self.settings.get(tenant_id)

    def upsert_settings(self, tenant_id, fields):
        self.settings.setdefault(tenant_id, {"tenant_id": tenant_id, "mode": "off", "tested_at": None}).update(fields)

    def list_tags(self, tenant_id):
        return [dict(t) for t in self.tags.values() if t["tenant_id"] == tenant_id]

    def create_tag(self, row):
        t = {"id": self._id(), "status": "active", "mode": "appointment", "allow_open_amount": False, **row}
        self.tags[t["id"]] = t
        return dict(t)

    def update_tag(self, tenant_id, tag_id, fields):
        self.tags[tag_id].update(fields)

    def recent_bills(self, tenant_id, limit=20):
        return [dict(b) for b in list(self.bills.values())[::-1] if b["tenant_id"] == tenant_id][:limit]

    def list_split_rules(self, tenant_id):
        return [dict(v, scope="default" if k[1] is None else f"staff:{k[1]}")
                for k, v in self.rules.items() if k[0] == tenant_id]

    def upsert_split_rule(self, tenant_id, scope, fields):
        staff = None if scope == "default" else scope.split(":", 1)[1]
        self.rules.setdefault((tenant_id, staff), {}).update(fields)

    def team_members(self, tenant_id):
        return self.members.get(tenant_id, [])

    # coach app
    def get_member(self, tenant_id, member_id):
        return next((m for m in self.members.get(tenant_id, []) if m["id"] == member_id), None)

    def put_enrol_code(self, tenant_id, member_id, code_hash, expires_at, created_by):
        self.codes = [c for c in self.codes if not (c["tenant_id"] == tenant_id and c["member_id"] == member_id)]
        self.codes.append({"id": self._id(), "tenant_id": tenant_id, "member_id": member_id, "code_hash": code_hash,
                           "expires_at": expires_at, "attempts": 0, "used_at": None})

    def get_enrol_code(self, tenant_id, member_id):
        rows = [c for c in self.codes if c["tenant_id"] == tenant_id and c["member_id"] == member_id]
        return dict(rows[-1]) if rows else None

    def _code(self, code_id):
        return next(c for c in self.codes if c["id"] == code_id)

    def bump_enrol_attempts(self, code_id):
        self._code(code_id)["attempts"] += 1

    def burn_enrol_code(self, code_id, now_iso):
        self._code(code_id)["used_at"] = now_iso

    def create_device(self, row):
        d = {"id": self._id(), "failed_pins": 0, "locked_until": None, "revoked_at": None,
             "last_seen_at": None, "created_at": "2026-10-06", **row}
        self.devices[d["id"]] = d
        return dict(d)

    def get_device(self, tenant_id, device_id):
        d = self.devices.get(device_id)
        return dict(d) if d and d["tenant_id"] == tenant_id else None

    def get_device_by_token_hash(self, token_hash):
        d = next((d for d in self.devices.values() if d["token_hash"] == token_hash), None)
        return dict(d) if d else None

    def update_device(self, tenant_id, device_id, fields):
        self.devices[device_id].update(fields)

    def list_devices(self, tenant_id):
        return [{k: v for k, v in d.items() if k not in ("token_hash", "pin_hash", "pin_salt")}
                for d in self.devices.values() if d["tenant_id"] == tenant_id]

    def upsert_push_sub(self, row):
        self.subs[row["endpoint"]] = dict(row)

    def delete_push_sub(self, endpoint):
        self.subs.pop(endpoint, None)

    def push_subs_for(self, tenant_id, member_id):
        return [dict(s) for s in self.subs.values() if s["tenant_id"] == tenant_id and s["member_id"] == member_id]

    def bill_updates(self, tenant_id, since_iso, staff_id):
        return [dict(b) for b in self.bills.values() if b["tenant_id"] == tenant_id
                and str(b.get("updated_at", "")) > since_iso and (not staff_id or b.get("staff_id") == staff_id)]

    def paid_detail(self, tenant_id, bill_id, party_id):
        s = next((s for s in self.sessions.values() if s.get("bill_id") == bill_id and s["state"] == "paid"), None)
        if not s:
            return {}
        out = {"tip_cents": s["tip_cents"], "total_cents": s["bill_cents"] + s["tip_cents"]}
        pay = self.payments.get(("payfast", "kb-" + s["id"]))
        if pay:
            out["paid_at"], out["ref"] = "2026-10-06T10:05:00+00:00", pay["id"][:8].upper()
        if pay and party_id:
            out["share_cents"] = sum(l["cents"] for l in self.ledger if l["payment_id"] == pay["id"] and l["party_id"] == party_id)
        return out

    # receipts
    def receipt_source(self, payment_id):
        pay = next((p for p in self.payments.values() if p["id"] == payment_id), None)
        if not pay:
            return None
        sess = self.sessions[pay["session_id"]]
        bill = self.bills.get(sess.get("bill_id"), {})
        return {"payment_id": pay["id"], "tenant_id": pay["tenant_id"], "paid_at": "2026-10-06T10:05:00+00:00",
                "nonce": pay.get("receipt_nonce", 0), "revoked_at": pay.get("receipt_revoked_at"),
                "bill_cents": sess["bill_cents"], "tip_cents": sess["tip_cents"], "description": bill.get("description"),
                "staff_id": bill.get("staff_id"), "is_test": bool(bill.get("is_test"))}

    def revoke_receipt(self, tenant_id, payment_id):
        next(p for p in self.payments.values() if p["id"] == payment_id)["receipt_revoked_at"] = "now"

    def merchant_vat(self, tenant_id):
        return self.vat.get(tenant_id, {"vat_number": None, "vat_registered": False})

    # unpaid-bill reminders
    PRE = ("claimed", "awaiting_amount", "awaiting_tip", "awaiting_confirm", "awaiting_payment")

    def stale_sessions(self, now_iso, limit=100):
        now = _ts(now_iso)
        return [dict(x) for x in self.sessions.values() if x["state"] in self.PRE and _ts(x["expires_at"]) < now][:limit]

    def enabled_tenants(self):
        return [t for t, v in self.settings.items() if v.get("mode") in ("testing", "live")]

    def bills_with_status(self, tenant_id, status, limit=100):
        return [dict(b) for b in self.bills.values() if b["tenant_id"] == tenant_id and b["status"] == status][:limit]

    def unpaid_bills(self, tenant_id):
        return [dict(b) for b in self.bills.values() if b["tenant_id"] == tenant_id and b["status"] in ("abandoned", "needs_follow_up")]

    def reminders_for_bill(self, tenant_id, bill_id):
        return sorted((dict(r) for r in self.reminders if r["bill_id"] == bill_id), key=lambda r: r["sent_at"])

    def insert_reminder(self, row):
        if row.get("kind") == "auto" and any(r["bill_id"] == row["bill_id"] and r["seq"] == row["seq"] and r["kind"] == "auto" for r in self.reminders):
            return None
        r = {"id": self._id(), **row}
        self.reminders.append(r)
        return dict(r)

    def update_reminder(self, reminder_id, fields):
        next(r for r in self.reminders if r["id"] == reminder_id).update(fields)

    def delete_reminder(self, reminder_id):
        self.reminders = [r for r in self.reminders if r["id"] != reminder_id]

    def cancel_other_sessions(self, tenant_id, bill_id, keep):
        for x in self.sessions.values():
            if x["tenant_id"] == tenant_id and x.get("bill_id") == bill_id and x["state"] in self.PRE and x["id"] != keep:
                x["state"] = "cancelled"

    def is_opted_out(self, tenant_id, phone):
        return "".join(c for c in phone if c.isdigit()) in self.opted_out

    # display
    def merchant_name(self, tenant_id):
        return self.names.get(tenant_id, "Bean and Brew Coffee")

    def staff_name(self, tenant_id, staff_id):
        m = next((m for m in self.members.get(tenant_id, []) if m["id"] == staff_id), None)
        if m:
            return (m.get("name") or "").split(" ")[0] or None
        return {"coach": "Sipho"}.get(staff_id)

    def tenant_wa_number(self, tenant_id):
        return self.wa.get(tenant_id, "+27 73 781 5979")

    def team_phones(self, tenant_id, staff_id):
        return self.team.get((tenant_id, staff_id), [])


class FakeMessenger:
    def __init__(self):
        self.sent = []          # (kind, tenant, phone, payload)

    async def text(self, tenant_id, phone, body):
        self.sent.append(("text", tenant_id, phone, body))
        return True

    async def buttons(self, tenant_id, phone, body, buttons):
        self.sent.append(("buttons", tenant_id, phone, (body, buttons)))
        return True

    async def list(self, tenant_id, phone, header, body, button, rows):
        self.sent.append(("list", tenant_id, phone, (body, rows)))
        return True

    async def template(self, tenant_id, phone, name, params):
        self.sent.append(("template", tenant_id, phone, (name, params)))
        return not getattr(self, "fail_next", False)

    def to(self, phone, kind=None):
        d = "".join(c for c in phone if c.isdigit())
        return [m for m in self.sent
                if "".join(c for c in m[2] if c.isdigit()) == d and (kind is None or m[0] == kind)]


class FakeGateway:
    def __init__(self):
        self.itn = None          # what verify_itn returns next (None = bad signature)
        self.created = []

    async def create_checkout(self, *, tenant_id, session, description):
        self.created.append(session["id"])
        return f"https://pay.example/checkout/{session['id']}"

    async def verify_itn(self, *, tenant_id, headers, body, form):
        return self.itn
