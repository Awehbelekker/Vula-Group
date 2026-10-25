"""vula/kb_records.py — one field of one money document, read off the filed record. Rows are
shaped like digg-demo's real vula_filed_documents (invoice numbers mostly only in the filename)."""
from unittest.mock import AsyncMock, patch

import pytest

from vula import kb_learning, kb_records as kr

T = "digg-demo"


def _row(doc_id, filename, category, supplier, date, cents=None, **f):
    fields = {"supplier": supplier, "date": date, **f}
    if cents is not None:
        fields["total_cents"] = cents
    return {"doc_id": doc_id, "filename": filename, "category": category, "fields": fields,
            "created_at": f"{date}T09:00:00+00:00"}


ROWS = [
    _row("sc1", "00097750.pdf", "Invoice", "SOLID CAPE (PTY) LTD", "2026-10-19", 120461,
         due_date="2026-11-18", vat_number="4560123456", bank="FNB", account_number="62001234567",
         branch_code="250655"),
    _row("sc2", "00097493.pdf", "Invoice", "SOLID CAPE (PTY) LTD", "2026-10-15", 1125931,
         vat_number="4560123456"),
    _row("scd", "00097749.pdf", "Delivery Note", "SOLID CAPE (PTY) LTD", "2026-10-19",
         reference="INV07180"),
    _row("diy", "Invoice_DO_IT_YOURSELF_HARDWARE_INV782520.PDF", "Invoice", "DO IT YOURSELF HARDWARE",
         "2026-10-14", 848300),
    _row("gd", "invoice 000833.pdf", "Invoice", "Golden Dee Solutions", "2026-10-10", 7999800,
         due_date="2026-12-10"),
    _row("gdq", "estimate 0000284.pdf", "Quote / Estimate", "Golden Dee Solutions", "2026-05-10", 7999800),
    _row("ex", "DIGG 2026 HPC14 (2).pdf", "Invoice", "AwehBelekker (Pty) Ltd t/a DIGG", "2026-10-07",
         4902539, amounts_exclude_vat=True),
]


@pytest.fixture
def turns(monkeypatch):
    rec = []
    monkeypatch.setattr(kb_learning, "record_turn", lambda *a, **k: rec.append(k))
    return rec


async def _ask(q, role="owner", names=None):
    with patch("vula.commerce.service._resolve_supplier_names", new=AsyncMock(return_value=names or [])):
        return await kr.answer(T, q, who="27820000000", role=role, rows=ROWS)


@pytest.mark.parametrize("q,field", [
    ("When is the Golden Dee invoice due?", "due"), ("What's Solid Cape's VAT number?", "vat_no"),
    ("Send me the banking details for Solid Cape", "bank"), ("What's the invoice number?", "number"),
    ("What date is the DIY invoice?", "date"), ("What's the total on invoice INV782520?", "total"),
    ("Is the delay due to the rain?", None), ("What is the slip rating of Affinity?", None)])
def test_what_is_asked(q, field):
    got = kr.asked_field(q)
    assert (got[0] if got else None) == field


@pytest.mark.asyncio
async def test_a_number_in_the_filename_finds_the_one_invoice(turns):
    assert await _ask("What's the total on invoice INV782520?") == \
        "Invoice INV782520 from DO IT YOURSELF HARDWARE, 14 Oct 2026: total R8 483.00."
    assert turns[0]["outcome"] == "answered" and turns[0]["answer_docs"] == ["diy"]


@pytest.mark.asyncio
async def test_the_kind_named_narrows_it(turns):
    # Golden Dee has an invoice and a quote; "invoice" picks the invoice.
    assert await _ask("When is the Golden Dee invoice due?") == \
        "Invoice 000833 from Golden Dee Solutions, 10 Oct 2026: due 10 Dec 2026 — R79 998.00."


@pytest.mark.asyncio
async def test_several_documents_ask_which_with_amounts_to_tell_them_apart(turns):
    out = await _ask("What's the total on the Solid Cape invoice?")
    assert out.startswith("I found more than one document")
    assert "1. Invoice 00097750 from SOLID CAPE (PTY) LTD, 19 Oct 2026, R1 204.61" in out
    assert "2. Invoice 00097493 from SOLID CAPE (PTY) LTD, 15 Oct 2026, R11 259.31" in out
    assert turns[0]["outcome"] == "clarify" and [c["doc_id"] for c in turns[0]["candidates"]] == ["sc1", "sc2"]


@pytest.mark.asyncio
async def test_latest_takes_the_newest(turns):
    assert "R1 204.61" in await _ask("What's the total on the latest Solid Cape invoice?")


@pytest.mark.asyncio
async def test_a_supplier_wide_field_they_all_agree_on_is_answered_once(turns):
    assert await _ask("What's Solid Cape's VAT number?") == \
        "SOLID CAPE (PTY) LTD: VAT number 4560123456 (from Invoice 00097750 from SOLID CAPE (PTY) LTD, 19 Oct 2026)."


@pytest.mark.asyncio
async def test_bank_details_always_carry_the_fraud_warning(turns):
    out = await _ask("Send me the banking details for Solid Cape")
    assert "FNB, account 62001234567, branch code 250655" in out
    assert "confirm bank details with the supplier" in out


@pytest.mark.asyncio
async def test_amounts_excluding_vat_say_so(turns):
    assert "(amounts exclude VAT)" in await _ask("What's the total on the HPC14 invoice?")


@pytest.mark.asyncio
async def test_an_alias_resolves_to_the_supplier(turns):
    out = await _ask("When is the Jack Hammer invoice due?", names=["Golden Dee Solutions", "Jack Hammer"])
    assert out and "Golden Dee Solutions" in out


@pytest.mark.parametrize("q", [
    "How much have we spent with Solid Cape this month?",   # a running total: supplier history
    "List all the Solid Cape invoices",
    "What is the total amount I need to pay for this receipt?",   # names nothing
    "What's the total on invoice 99999999?",                # a number nothing carries: no guessing
    "When is the Pudlo invoice due?",                       # no such supplier on file
    "What's Golden Dee's VAT number?",                      # not on the record
])
@pytest.mark.asyncio
async def test_anything_not_exact_goes_the_normal_way(q, turns):
    assert await _ask(q) is None
    assert turns == []


@pytest.mark.asyncio
async def test_customers_never_get_record_answers(turns):
    assert await _ask("When is the Golden Dee invoice due?", role=None) is None


def test_a_pick_is_answered_from_that_documents_fields():
    from unittest.mock import MagicMock
    q = MagicMock()
    q.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [ROWS[1]]
    with patch("vula.commerce.service._client", return_value=MagicMock(table=lambda n: q)):
        assert kr.answer_for_doc(T, "What's the total on the Solid Cape invoice?", "sc2") == \
            "Invoice 00097493 from SOLID CAPE (PTY) LTD, 15 Oct 2026: total R11 259.31."
