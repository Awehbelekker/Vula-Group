"""Public cart/checkout endpoints refuse server-side session ids (2026-09-25 review): a WhatsApp
cart's session id is the customer's phone number, so anyone knowing it could read or alter it."""
import pytest
from fastapi import HTTPException

from vula.api.commerce import _require_public_session


@pytest.mark.parametrize("sid", ["27821234567", "+27821234567", "0821234567", "manual-abc123", "admin:2782"])
def test_server_sessions_refused(sid):
    with pytest.raises(HTTPException) as exc:
        _require_public_session(sid)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("sid", ["3f1c2a9e-7b1d-4c55-9a51-0c2b8d1e4f00", "sess_a8Kx92", "web-1717171717171"])
def test_storefront_sessions_allowed(sid):
    _require_public_session(sid)
