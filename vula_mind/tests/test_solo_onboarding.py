"""Fast onboarding and the solo-operator tier (finance brief, capability 7).

- 'solo': a one-person business with only the money side switched on.
- The go-live checklist counts payment set-up from a gateway, EFT text OR the bank details on the
  invoice settings, and measures the first invoice sent against signup (target: 10 minutes).
"""
from vula.api import master, tenants


def test_solo_is_finance_only():
    mods = set(tenants.BUSINESS_TYPES["solo"]["modules"])
    assert {"invoices", "payments", "finances"} <= mods
    assert not mods & {"products", "orders", "projects", "fieldops", "broadcasts", "pages"}
    assert tenants.valid_business_type("Solo") == "solo"


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _t):
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})()


def test_first_invoice_is_timed_from_signup():
    cfg = {"created_at": "2026-10-06T08:00:00+00:00"}
    db = _Q([{"invoice_number": "SOLO-INV-00001", "sent_at": "2026-10-06T08:07:30+00:00"}])
    assert master._first_invoice_sent(db, "t", cfg) == "SOLO-INV-00001, 7 min after signup"
    assert master._first_invoice_sent(_Q([]), "t", cfg) is None
