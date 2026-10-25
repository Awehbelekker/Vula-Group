"""vula/commerce/supplier_details.py — a supplier's blanks filled from their own invoices; bank
details that differ are flagged, never applied."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import supplier_details as sd

BILL = {"tax_id": "4560123456", "customer_vat_number": "4290114281", "supplier_phone": "021 555 0101",
        "supplier_email": "accounts@solidcape.co.za",
        "supplier_bank": {"bank_name": "FNB", "account_name": "Solid Cape", "account_number": "62001234567",
                          "branch_code": "250655"}}


def test_blanks_are_filled_from_the_invoice():
    up, change = sd.updates_from_document({"id": "s1", "name": "Solid Cape"}, BILL, "doc1")
    assert change is None
    assert up["tax_id"] == "4560123456" and up["contact_phone"] == "021 555 0101"
    assert up["contact_email"] == "accounts@solidcape.co.za"
    assert (up["bank_name"], up["bank_account_number"], up["bank_branch_code"], up["bank_details_doc"]) == \
        ("FNB", "62001234567", "250655", "doc1")


def test_nothing_on_the_record_is_overwritten():
    sup = {"id": "s1", "tax_id": "4111111111", "contact_phone": "082 000 0000",
           "contact_email": "x@y.co.za", "bank_account_number": "62001234567"}
    up, change = sd.updates_from_document(sup, BILL)
    assert up == {} and change is None          # same account: no change


def test_different_bank_details_are_flagged_not_applied():
    sup = {"id": "s1", "bank_name": "FNB", "bank_account_number": "62009999999"}
    up, change = sd.updates_from_document(sup, {**BILL, "supplier_bank": {"bank_name": "Capitec",
                                                                         "account_number": "1234567890"}})
    assert "bank_account_number" not in up
    assert change == {"on_file": "…9999", "on_invoice": "…7890", "on_file_bank": "FNB", "on_invoice_bank": "Capitec"}
    msg = sd.bank_change_message("Solid Cape", change, 120461)
    assert "Capitec …7890" in msg and "FNB …9999" in msg and "R1 204.61" in msg
    assert "62009999999" not in msg and "1234567890" not in msg      # never the full numbers


@pytest.mark.parametrize("fields", [
    {"tax_id": "41202400"},                                         # not a valid SA VAT number
    {"tax_id": "4290114281", "customer_vat_number": "4290114281"},  # it's the customer's
    {"supplier_email": "not-an-email"}, {"supplier_phone": "12"},
    {"supplier_bank": {"bank_name": "FNB"}},                        # no account number
])
def test_doubtful_details_are_not_used(fields):
    assert sd.updates_from_document({"id": "s1"}, fields) == ({}, None)


def test_the_business_own_vat_is_never_given_to_a_supplier():
    up, _ = sd.updates_from_document({"id": "s1"}, {"tax_id": "4120206471"}, exclude_vats={"4120206471"})
    assert up == {}


def test_apply_writes_and_reports_filled_fields():
    table = MagicMock()
    with patch("vula.commerce.service._client", return_value=MagicMock(table=lambda n: table)):
        out = sd.apply_document_details("digg-demo", {"id": "s1", "name": "Solid Cape"}, BILL, "doc1")
    assert set(out["filled"]) == {"tax_id", "contact_phone", "contact_email", "bank_name",
                                  "bank_account_name", "bank_account_number", "bank_branch_code"}
    table.update.assert_called_once()


def test_before_the_migration_the_rest_still_saves():
    calls = []

    class _T:
        def update(self, vals):
            calls.append(vals)
            self.vals = vals
            return self

        def eq(self, *a):
            return self

        def execute(self):
            if "bank_account_number" in self.vals:
                raise RuntimeError("column does not exist")
    with patch("vula.commerce.service._client", return_value=MagicMock(table=lambda n: _T())):
        out = sd.apply_document_details("digg-demo", {"id": "s1"}, BILL, "doc1")
    assert "bank_account_number" not in out["filled"] and "tax_id" in out["filled"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_bank_change_alerts_the_finance_team_once_per_document():
    with patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as nt:
        await sd.alert_bank_change("digg-demo", "Solid Cape", {"on_file": "…9999", "on_invoice": "…7890"}, "doc1")
    args, kwargs = nt.await_args
    assert args[1] == "bank_review" and kwargs["idem_key"] == "bank-change:doc1"


def test_backfill_fills_from_newest_skips_own_vat_and_counts_history():
    sups = [{"id": "s1", "name": "Solid Cape"}, {"id": "s2", "name": "A"}, {"id": "s3", "name": "B"}]
    rows = [
        {"id": "d3", "supplier_id": "s1", "created_at": "2026-10-20", "category": "Invoice",
         "fields": {"supplier": "Solid Cape", "supplier_bank": {"bank_name": "FNB", "account_number": "62001234567"}}},
        {"id": "d2", "supplier_id": "s1", "created_at": "2026-09-01", "category": "Invoice",
         "fields": {"supplier": "Solid Cape", "tax_id": "4120206471",
                    "supplier_bank": {"bank_name": "ABSA", "account_number": "40501112222"}}},
        {"id": "d4", "supplier_id": "s2", "created_at": "2026-09-01", "category": "Invoice",
         "fields": {"supplier": "A", "tax_id": "4120206471"}},
        {"id": "d5", "supplier_id": "s3", "created_at": "2026-09-01", "category": "Invoice",
         "fields": {"supplier": "B", "tax_id": "4120206471"}},
    ]
    updates = []

    class _T:
        def __init__(self, name):
            self.name, self.vals = name, None

        def select(self, *a):
            return self

        def update(self, vals):
            self.vals = vals
            return self

        def eq(self, *a):
            return self

        def in_(self, *a):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, *a):
            return self

        def execute(self):
            if self.vals is not None:
                updates.append(self.vals)
                return MagicMock(data=[])
            return MagicMock(data=sups if self.name == "commerce_suppliers" else rows)
    with patch("vula.commerce.service._client", return_value=MagicMock(table=lambda n: _T(n))):
        out = sd.backfill("digg-demo")
    assert out["bank_differences"] == 1                   # the older ABSA account, counted not sent
    assert out["filled"] == {"bank_account_number": 1}    # 4120206471 is on 3 suppliers' bills: own
    assert updates[0]["bank_account_number"] == "62001234567"
