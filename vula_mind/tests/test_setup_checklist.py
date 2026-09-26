"""The go-live checklist counts APPROVED templates, knows about VAT/gateways/docs, and each
step names the tab that fixes it (2026-09-25 review)."""
from unittest.mock import patch

from vula.api import master


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


DATA = {
    "vula_tenant_config": [{"tenant_id": "t1", "display_name": "T1", "modules": ["orders"]}],
    "commerce_wa_templates": [{"status": "PENDING"}, {"status": "APPROVED"}],
    "vula_yoco_accounts": [{"id": 1}],
    "commerce_invoice_settings": [{"vat_registered": None}],
}


def test_checklist_steps():
    db = type("C", (), {"table": lambda self, n: _Q(DATA.get(n, []))})()
    with patch.object(master, "_client", return_value=db):
        out = master.setup_checklist("t1")
    steps = {s["id"]: s for s in out["steps"]}
    assert steps["templates"]["done"] and "1 approved of 2" in steps["templates"]["detail"]
    assert steps["payments"]["done"]            # Yoco counts
    assert not steps["vat"]["done"]             # never confirmed
    assert all(s.get("tab") for s in out["steps"])
