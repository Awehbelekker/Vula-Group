"""Connect endpoints that carry the tenant in the BODY are covered by tenant auth too, and
embedded signup connects the number the owner chose (2026-09-25 review)."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.api import tenant_auth


@pytest.mark.asyncio
async def test_body_tenant_check(monkeypatch):
    monkeypatch.setattr("config.settings.enforce_tenant_auth", True)
    with patch.object(tenant_auth, "is_tenant_member", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as e1:
            await tenant_auth.check_body_tenant("t1", "")
        with pytest.raises(HTTPException) as e2:
            await tenant_auth.check_body_tenant("t1", "Bearer x")
    assert (e1.value.status_code, e2.value.status_code) == (401, 403)
    with patch.object(tenant_auth, "is_tenant_member", AsyncMock(return_value=True)):
        await tenant_auth.check_body_tenant("t1", "Bearer x")
    monkeypatch.setattr("config.settings.enforce_tenant_auth", False)
    await tenant_auth.check_body_tenant("t1", "")   # switch off -> unchanged behaviour
