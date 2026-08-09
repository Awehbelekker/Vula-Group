"""Tests for structured progress claims / interim payment certificates (backlog item,
2026-08-09). JBCC-style: each claim states the CUMULATIVE value of work done to date;
retention and "this payment" must always be computed from that + the previous claim's
certified-to-date total, never trusted from the caller."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.integrations.progress_claims import (
    certify_claim,
    convert_claim_to_invoice,
    create_claim,
    list_claims,
)


def _mock_db(claims=None, insert_return=None):
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value \
        .execute.return_value = MagicMock(data=claims or [])
    if insert_return is not None:
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[insert_return])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db, mock_table


# ── list_claims ───────────────────────────────────────────────────────────────

def test_list_claims_empty_on_error():
    with patch("vula.integrations.progress_claims._client", side_effect=Exception("no table")):
        assert list_claims("off-the-hook", "HPC_Bokaap") == []


# ── create_claim: first claim ────────────────────────────────────────────────

def test_create_claim_first_claim_computes_retention_and_payment():
    mock_db, mock_table = _mock_db(claims=[])
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        create_claim("off-the-hook", "HPC_Bokaap", 100_000_00, retention_pct=5.0)

    inserted = mock_table.insert.call_args.args[0]
    assert inserted["claim_number"] == 1
    assert inserted["cumulative_value_cents"] == 100_000_00
    assert inserted["retention_cents"] == 5_000_00          # 5% of 100k
    assert inserted["certified_to_date_cents"] == 95_000_00
    assert inserted["previous_certified_cents"] == 0
    assert inserted["this_payment_cents"] == 95_000_00       # nothing certified before
    assert inserted["status"] == "draft"


def test_create_claim_rejects_non_positive_value():
    mock_db, _ = _mock_db(claims=[])
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        with pytest.raises(ValueError, match="positive"):
            create_claim("off-the-hook", "HPC_Bokaap", 0)


# ── create_claim: second claim (cumulative math) ─────────────────────────────

def test_create_claim_second_claim_carries_forward_previous_certified():
    first = {
        "claim_number": 1, "cumulative_value_cents": 100_000_00,
        "certified_to_date_cents": 95_000_00,
    }
    mock_db, mock_table = _mock_db(claims=[first])
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        create_claim("off-the-hook", "HPC_Bokaap", 200_000_00, retention_pct=5.0)

    inserted = mock_table.insert.call_args.args[0]
    assert inserted["claim_number"] == 2
    assert inserted["retention_cents"] == 10_000_00           # 5% of 200k
    assert inserted["certified_to_date_cents"] == 190_000_00
    assert inserted["previous_certified_cents"] == 95_000_00
    assert inserted["this_payment_cents"] == 95_000_00        # 190k certified - 95k already certified


def test_create_claim_rejects_lower_cumulative_than_previous():
    first = {
        "claim_number": 1, "cumulative_value_cents": 200_000_00,
        "certified_to_date_cents": 190_000_00,
    }
    mock_db, _ = _mock_db(claims=[first])
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        with pytest.raises(ValueError, match="less than the previous"):
            create_claim("off-the-hook", "HPC_Bokaap", 100_000_00)


# ── certify_claim ────────────────────────────────────────────────────────────

def test_certify_claim_updates_status():
    mock_table = MagicMock()
    mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"id": "c1", "status": "certified"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.integrations.progress_claims._client", return_value=mock_db),
        patch("vula.commerce.service._now", return_value="2026-08-09T00:00:00Z"),
    ):
        result = certify_claim("off-the-hook", "c1")
    assert result["status"] == "certified"


def test_certify_claim_not_found_raises():
    mock_table = MagicMock()
    mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.integrations.progress_claims._client", return_value=mock_db),
        patch("vula.commerce.service._now", return_value="2026-08-09T00:00:00Z"),
    ):
        with pytest.raises(ValueError, match="not found"):
            certify_claim("off-the-hook", "missing")


# ── convert_claim_to_invoice ──────────────────────────────────────────────────

def _claim_row(**overrides):
    row = {
        "id": "c1", "claim_number": 2, "project": "HPC_Bokaap",
        "cumulative_value_cents": 200_000_00, "retention_pct": 5.0,
        "this_payment_cents": 95_000_00, "linked_invoice_id": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_convert_claim_to_invoice_creates_invoice_for_this_payment():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[_claim_row()])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    fake_invoice = {"id": "inv1", "total_cents": 109250}

    with (
        patch("vula.integrations.progress_claims._client", return_value=mock_db),
        patch("vula.commerce.service.create_invoice", new=AsyncMock(return_value=fake_invoice)) as mock_create,
    ):
        invoice = await convert_claim_to_invoice(
            "off-the-hook", "c1", {"name": "Bo-Kaap Client", "phone": "0821234567"})

    assert invoice == fake_invoice
    call_body = mock_create.call_args.args[1]
    assert call_body["line_items"][0]["unit_price_cents"] == 95_000_00
    assert call_body["project"] == "HPC_Bokaap"
    assert call_body["customer_name"] == "Bo-Kaap Client"
    # Claim gets linked back to the new invoice + marked invoiced
    mock_table.update.assert_called_once()
    upd = mock_table.update.call_args.args[0]
    assert upd["linked_invoice_id"] == "inv1"
    assert upd["status"] == "invoiced"


@pytest.mark.asyncio
async def test_convert_claim_to_invoice_rejects_already_linked():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[_claim_row(linked_invoice_id="inv0")])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        with pytest.raises(ValueError, match="already has an invoice"):
            await convert_claim_to_invoice("off-the-hook", "c1", {"name": "Client"})


@pytest.mark.asyncio
async def test_convert_claim_to_invoice_requires_customer_name():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[_claim_row()])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        with pytest.raises(ValueError, match="name is required"):
            await convert_claim_to_invoice("off-the-hook", "c1", {})


@pytest.mark.asyncio
async def test_convert_claim_to_invoice_rejects_nothing_to_invoice():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[_claim_row(this_payment_cents=0)])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.integrations.progress_claims._client", return_value=mock_db):
        with pytest.raises(ValueError, match="Nothing to invoice"):
            await convert_claim_to_invoice("off-the-hook", "c1", {"name": "Client"})


# ── project_financials: latest_claim wiring ──────────────────────────────────

def test_project_financials_surfaces_latest_claim():
    from vula.integrations.finances import project_financials
    last_claim = {
        "claim_number": 2, "status": "certified", "cumulative_value_cents": 200_000_00,
        "retention_cents": 10_000_00, "certified_to_date_cents": 190_000_00,
        "this_payment_cents": 95_000_00,
    }
    mock_table = MagicMock()  # every empty-ish lookup this function makes falls through safely
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.integrations.finances._client", return_value=mock_db),
        patch("vula.integrations.progress_claims.list_claims", return_value=[last_claim]),
    ):
        result = project_financials("off-the-hook", "HPC_Bokaap")

    assert result["latest_claim"]["claim_number"] == 2
    assert result["latest_claim"]["this_payment"] == 95_000.0
    assert result["latest_claim"]["retention_held"] == 10_000.0
