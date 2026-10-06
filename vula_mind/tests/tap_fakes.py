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
                and b["tag_id"] == tag_id and b["status"] in ("open", "claimed")]

    def get_bill(self, tenant_id, bill_id):
        b = self.bills.get(bill_id)
        return dict(b) if b and b["tenant_id"] == tenant_id else None

    def create_bill(self, row):
        b = {"id": self._id(), "code_attempts": 0, "code_locked_until": None, "customer_hash": None,
             "claimed_by_hash": None, "bill_code": None, **row}
        self.bills[b["id"]] = b
        return dict(b)

    def cas_bill(self, tenant_id, bill_id, *, expected, new, fields=None):
        b = self.bills.get(bill_id)
        if not b or b["tenant_id"] != tenant_id or b["status"] != expected:
            return False
        b.update(fields or {})
        b["status"] = new
        return True

    def update_bill(self, tenant_id, bill_id, fields):
        self.bills[bill_id].update(fields)

    # sessions
    def create_session(self, row):
        s = {"id": self._id(), "pay_nonce_hash": None, "expecting": None, "staff_ref": None,
             "checkout_ref": None, **row}
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
        p = {"id": self._id(), **row}
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

    # display
    def merchant_name(self, tenant_id):
        return self.names.get(tenant_id, "Bean and Brew Coffee")

    def staff_name(self, tenant_id, staff_id):
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
