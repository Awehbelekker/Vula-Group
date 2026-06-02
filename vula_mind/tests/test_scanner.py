import pytest
from unittest.mock import patch, MagicMock
from vula.api.commerce import ScanRequest, admin_smart_scan, admin_scan_commit

TENANT = "off-the-hook"

@pytest.mark.asyncio
async def test_scan_request_parsing():
    """Verify ScanRequest model works."""
    req = ScanRequest(image_base64="data:image/jpeg;base64,abc", doc_type="receipt", tenant_id=TENANT)
    assert req.doc_type == "receipt"
    assert req.image_base64.startswith("data:")

@pytest.mark.asyncio
async def test_admin_scan_flow_no_litellm():
    """Verify the endpoint handles missing litellm or errors gracefully."""
    with patch("litellm.acompletion", side_error=ImportError):
        body = ScanRequest(image_base64="abc", doc_type="receipt")
        with pytest.raises(Exception): # Fastapi would catch this and 500
             await admin_smart_scan(TENANT, body)

@pytest.mark.asyncio
async def test_admin_scan_commit_mocked():
    """Test the commit logic with a mocked DB."""
    # Build a more robust mock chain for Supabase
    mock_db = MagicMock()

    # Mock supplier lookup to return no data
    mock_db.table.return_value.select.return_value.eq.return_value.ilike.return_value.limit.return_value.execute.return_value.data = []
    # Fallback alias search
    mock_db.table.return_value.select.return_value.eq.return_value.contains.return_value.limit.return_value.execute.return_value.data = []

    # Mock the insert
    mock_db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "new-rec"}]

    extracted = {
        "doc_type": "receipt",
        "supplier": "Test Supplier",
        "total_cents": 1000,
        "date": "2024-01-01"
    }

    with patch("vula.commerce.service._client", return_value=mock_db):
        body = {"extracted": extracted, "auto_commit": True}
        res = await admin_scan_commit(TENANT, body)
        assert res["ok"] is True
        assert res["committed"] is True
        assert res["record_type"] == "expense"
        assert "Test Supplier" in res["message"]  # More robust than 'Captured' since it might be 'OVERDUE'
