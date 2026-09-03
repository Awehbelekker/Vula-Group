"""A proof of payment for a bill the OWNER paid is money OUT.

2026-09-03. stage_pop_for_review hardcoded direction="in", so when Staci photographed the
confirmation for a supplier she had just paid, it was staged as a customer paying HER and
matched against her own outstanding invoices. Confirming it would have marked one of her
sales invoices paid — inventing revenue and leaving the supplier's bill open, the same class
of failure as the 51 misfiled OTH invoices and the ledger's unconditional post_invoice_paid.

The PDF path already handled this (whatsapp.py::_SUPPLIER_CHECK_FIELD, built for DIGG's real
FNB payment notifications); the photo path did not — and a photo is what an owner on the move
actually sends.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import bank_rec, bank_review

TENANT = "off-the-hook"


def _db(bills=None, inserted=None, updated=None):
    """Minimal Supabase double: commerce_suppliers/commerce_invoices reads, insert capture."""
    bills = bills or []

    class _Q:
        def __init__(self, table):
            self.table_name, self._filters = table, {}

        def select(self, *a, **k):
            return self

        def insert(self, row):
            if inserted is not None:
                inserted.append(row)
            return self

        def update(self, patch_):
            if updated is not None:
                updated.append(patch_)
            return self

        def eq(self, col, val):
            self._filters[col] = val
            return self

        def in_(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self.table_name == "commerce_suppliers":
                return MagicMock(data=[{"name": "Atlantis Seafood"}])
            if self.table_name == "commerce_invoices":
                return MagicMock(data=list(bills))
            return MagicMock(data=[])

    return MagicMock(table=lambda name: _Q(name))


BILL = {"id": "bill-1", "invoice_number": "BILL-0007", "supplier": "Atlantis Seafood",
        "total_cents": 452000, "status": "draft", "doc_type": "invoice", "direction": "inbound"}


def _tenant_named(name):
    return patch("vula.api.tenants.get_config", lambda t: {"display_name": name})


# ── which way did the money go ───────────────────────────────────────────────────

@pytest.mark.parametrize("payee,expected,confident", [
    ("Off the Hook", "in", True),            # a customer paying us
    ("Off the Hook (Pty) Ltd", "in", True),  # same business, legal suffix
    ("Atlantis Seafood", "out", True),       # a supplier we know
    ("Someone Random", "in", False),         # unrecognised — prior behaviour, but flagged
    ("", "in", False),                       # nothing to go on
])
def test_direction_follows_who_received_the_money(payee, expected, confident):
    with patch.object(bank_rec, "_client", lambda: _db(bills=[BILL])), _tenant_named("Off the Hook"):
        direction, is_confident, _reason = bank_rec.classify_pop_direction(TENANT, payee)
    assert direction == expected
    assert is_confident is confident


def test_a_supplier_we_owe_counts_even_when_not_in_the_supplier_table():
    """A bill can be scanned in before the supplier record exists — the open bill is itself
    proof of the relationship."""
    with patch.object(bank_rec, "_client", lambda: _db(bills=[dict(BILL, supplier="Cape Ice Co")])), \
         _tenant_named("Off the Hook"):
        direction, confident, _ = bank_rec.classify_pop_direction(TENANT, "Cape Ice Co")
    assert (direction, confident) == ("out", True)


def test_tenant_name_is_read_from_config_not_the_slug():
    """Passing the slug instead of the real display_name is what produced a phantom
    R5,019,897 misfiling on DIGG — the id must not be the only thing checked."""
    with patch.object(bank_rec, "_client", lambda: _db()), _tenant_named("DIGG Architecture"):
        direction, confident, _ = bank_rec.classify_pop_direction("digg-demo", "DIGG Architecture")
    assert (direction, confident) == ("in", True)


# ── staging ──────────────────────────────────────────────────────────────────────

def test_a_supplier_pop_is_staged_as_money_out_against_a_bill():
    rows = []
    with patch.object(bank_rec, "_client", lambda: _db(bills=[BILL], inserted=rows)), \
         _tenant_named("Off the Hook"):
        reply = bank_rec.stage_pop_for_review(TENANT, 452000, "2026-09-03", None,
                                              "Atlantis Seafood", sender_phone="27737815979")
    assert rows[0]["direction"] == "out"
    assert rows[0]["proposed_match_type"] == "supplier_bill"
    assert rows[0]["proposed_match_id"] == "bill-1"
    assert "BILL-0007" in reply and "R4,520.00" in reply


def test_a_customer_pop_still_takes_the_money_in_path_untouched():
    with patch.object(bank_rec, "_client", lambda: _db()), _tenant_named("Off the Hook"), \
         patch.object(bank_rec, "propose_pop_match", return_value=None) as propose:
        reply = bank_rec.stage_pop_for_review(TENANT, 25000, None, None, "Off the Hook",
                                              sender_phone="27831112222")
    propose.assert_called_once()
    assert "which order or invoice" in reply


def test_an_unmatched_supplier_pop_still_asks_rather_than_guessing():
    rows = []
    with patch.object(bank_rec, "_client", lambda: _db(bills=[BILL], inserted=rows)), \
         _tenant_named("Off the Hook"):
        reply = bank_rec.stage_pop_for_review(TENANT, 999, None, None, "Atlantis Seafood")
    assert rows[0]["proposed_match_id"] is None
    assert "couldn't find a matching open bill" in reply


# ── confirming ───────────────────────────────────────────────────────────────────

def _asked_out_db(bills, updated):
    base = _db(bills=bills, updated=updated)
    txn = {"id": "txn-1", "amount_cents": 452000, "direction": "out",
           "proposed_match_type": "supplier_bill", "proposed_match_id": "bill-1",
           "source_file": "whatsapp_pop", "reference": None}

    class _Q:
        def __init__(self, name):
            self.name, self.f = name, {}

        def select(self, *a, **k):
            return self

        def update(self, patch_):
            updated.append(patch_)
            return self

        def eq(self, c, v):
            self.f[c] = v
            return self

        def in_(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self.name == "commerce_bank_transactions":
                return MagicMock(data=[txn] if self.f.get("direction") == "out" else [])
            if self.name == "commerce_invoices":
                return MagicMock(data=list(bills))
            if self.name == "commerce_suppliers":
                return MagicMock(data=[{"name": "Atlantis Seafood"}])
            return MagicMock(data=[])

    base.table = lambda name: _Q(name)
    return base


@pytest.mark.asyncio
async def test_yes_settles_the_bill_not_a_sales_invoice():
    """update_invoice_status reads the bill's direction and posts the payables side — the
    check that stops a supplier payment crediting `sales`."""
    updated = []
    db = _asked_out_db([BILL], updated)
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db), \
         patch("vula.commerce.service.update_invoice_status", AsyncMock()) as mark:
        reply = await bank_review.handle_client_answer(TENANT, "yes")
    assert mark.await_args[0][1] == "bill-1"
    assert mark.await_args[0][2] == "paid"
    assert "BILL-0007" in reply and "Atlantis Seafood" in reply
    assert {"matched_invoice_id": "bill-1", "match_status": "matched"} in updated


@pytest.mark.asyncio
async def test_a_bill_number_beats_the_proposal():
    updated = []
    other = dict(BILL, id="bill-2", invoice_number="BILL-0009", total_cents=110000)
    db = _asked_out_db([BILL, other], updated)
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db), \
         patch("vula.commerce.service.update_invoice_status", AsyncMock()) as mark:
        await bank_review.handle_client_answer(TENANT, "BILL-0009")
    assert mark.await_args[0][1] == "bill-2"


@pytest.mark.asyncio
async def test_a_stale_proposal_is_not_confirmed_blindly():
    """The bill closed between asking and answering — say so rather than settling something
    the owner can no longer see."""
    db = _asked_out_db([], [])
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db), \
         patch("vula.commerce.service.update_invoice_status", AsyncMock()) as mark:
        reply = await bank_review.handle_client_answer(TENANT, "yes")
    mark.assert_not_awaited()
    assert "isn't open any more" in reply


# ── the ambiguous payee (the real DIGG row) ──────────────────────────────────────

def test_an_unknown_payee_is_asked_which_way_the_money_went():
    """digg-demo has one real POP: R3,000 to 'Mr Onito Tiler', a subcontractor paid ad hoc.
    He is in neither the 49-name supplier table nor any of the 135 open bills, so a name
    lookup alone can't classify him — and 'which order is this for?' is the wrong question.
    It sat 'asked' and unanswered from 2026-08-17."""
    rows = []
    with patch.object(bank_rec, "_client", lambda: _db(inserted=rows)), \
         _tenant_named("DIGG Architecture"), \
         patch.object(bank_rec, "propose_pop_match", return_value=None):
        reply = bank_rec.stage_pop_for_review("digg-demo", 300000, "2026-08-17", None,
                                              "Mr Onito Tiler")
    assert "Did you pay them" in reply
    assert "Mr Onito Tiler" in reply and "R3,000.00" in reply
    assert rows[0]["direction"] == "in", "stays in until answered — no guessing"


@pytest.mark.asyncio
async def test_i_paid_flips_it_to_money_out():
    updated = []
    txn = {"id": "txn-9", "amount_cents": 300000, "direction": "in", "match_status": "asked",
           "source_file": "whatsapp_pop", "reference": None,
           "description": "WhatsApp proof of payment — Mr Onito Tiler"}

    class _Q:
        def __init__(self, name):
            self.name, self.f = name, {}

        def select(self, *a, **k):
            return self

        def update(self, p):
            updated.append(p)
            return self

        def eq(self, c, v):
            self.f[c] = v
            return self

        def in_(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self.name == "commerce_bank_transactions":
                return MagicMock(data=[txn] if self.f.get("direction") == "in" else [])
            return MagicMock(data=[])

    db = MagicMock(table=lambda n: _Q(n))
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db):
        reply = await bank_review.handle_client_answer("digg-demo", "I paid")
    assert {"direction": "out", "proposed_match_type": None, "proposed_match_id": None} in updated
    assert "Money out" in reply and "Mr Onito Tiler" in reply


@pytest.mark.asyncio
async def test_a_stray_i_paid_on_a_bank_statement_row_is_not_flipped():
    """Only a POP row asked the direction question — a statement credit must not be flipped
    by someone typing the same words."""
    txn = {"id": "t", "amount_cents": 5000, "direction": "in", "match_status": "asked",
           "source_file": "capitec_july.pdf", "description": "EFT CREDIT"}
    updated = []

    class _Q:
        def __init__(self, name):
            self.name, self.f = name, {}

        def select(self, *a, **k):
            return self

        def update(self, p):
            updated.append(p)
            return self

        def eq(self, c, v):
            self.f[c] = v
            return self

        def ilike(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            if self.name == "commerce_bank_transactions":
                return MagicMock(data=[txn] if self.f.get("direction") == "in" else [])
            return MagicMock(data=[])

    db = MagicMock(table=lambda n: _Q(n))
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db):
        await bank_review.handle_client_answer(TENANT, "i paid")
    assert not any(p.get("direction") == "out" for p in updated)


@pytest.mark.asyncio
async def test_skip_leaves_the_bill_alone():
    updated = []
    db = _asked_out_db([BILL], updated)
    with patch.object(bank_review, "_client", lambda: db), \
         patch.object(bank_rec, "_client", lambda: db), \
         patch("vula.commerce.service.update_invoice_status", AsyncMock()) as mark:
        reply = await bank_review.handle_client_answer(TENANT, "skip")
    mark.assert_not_awaited()
    assert {"match_status": "ignored"} in updated
    assert "Skipped" in reply
