"""vula/api/connections_api.py — a business's app connections, and the master view of all of them.

  GET  /v1/connections/{tenant_id}          this business's connections and their state (members only,
                                            via the tenant guard in server.py)
  GET  /v1/master/connections               every business, as last checked (master only)
  POST /v1/master/connections/check         run the check now (master only); owners are told what broke

See vula/connections.py. Nothing here returns a token or a secret.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from vula.api.master_auth import require_master

router = APIRouter(tags=["connections"])
master_router = APIRouter(tags=["connections"], dependencies=[Depends(require_master)])


@router.get("/{tenant_id}")
async def tenant_connections(tenant_id: str) -> dict:
    from vula import connections
    items = connections.state(tenant_id)
    return {"tenant_id": tenant_id, "connections": items,
            "broken": sum(1 for c in items if c["state"] == "broken")}


@master_router.get("")
async def all_connections() -> dict:
    from vula import connections
    rows = connections.overview()
    return {"connections": rows, "broken": sum(1 for r in rows if r["state"] == "broken"),
            "unavailable": sorted({r["provider"] for r in rows if r["state"] == "unavailable"}),
            "one_tap": {p: connections.one_tap(p) for p in ("clickup", "google", "microsoft", "dynamics365")}}


@master_router.post("/check")
async def check_now() -> dict:
    from vula import connections
    return await connections.check_all()
