"""vula/api/accountant.py — an accountant's own view across their clients (19 Nov 2026).

  GET /v1/accountant/clients   the businesses this login is the accountant for (name, logo), so the
                               dashboard can let one accountant move between their clients.

Only ever the caller's own businesses, read from their own login rows (vula/api/tenant_auth.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(tags=["accountant"])


@router.get("/clients")
async def clients(authorization: str = Header(default="")) -> dict:
    from vula.api.tenant_auth import accountant_login
    acc = await accountant_login(authorization)
    if not acc:
        if not (authorization or "").removeprefix("Bearer ").strip():
            raise HTTPException(status_code=401, detail="Sign in required.")
        raise HTTPException(status_code=403, detail="This login isn't an accountant's.")
    from vula.api import tenants
    out = []
    for tid in sorted(acc["tenants"]):
        try:
            if not tenants.is_active(tid):
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append({"tenant_id": tid, "name": tenants.display_name(tid)})
    return {"clients": out}
