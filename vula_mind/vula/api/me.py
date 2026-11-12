"""vula/api/me.py — what belongs to the signed-in person, not to the business.

Everything here acts on the CALLER, taken from their verified login (vula/identity.py). There is
no member id in any path or body: nobody — not a colleague, not the owner — can read or change
another person's settings through these routes.

Mounted under /v1/commerce, so the paths are /v1/commerce/{tenant_id}/admin/me/… and pass the
same membership guard as the rest of the tenant admin API.

  GET/PATCH  /{tenant_id}/admin/me/prefs    how Vula talks to me (core/voice.py)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(tags=["me"])


async def _me(tenant_id: str, request: Request):
    """The signed-in person, or 401. Never falls back to "someone": these are personal routes."""
    from vula.identity import person_for_login
    person = await person_for_login(request.headers.get("authorization", ""), tenant_id)
    if person is None or not person.key:
        raise HTTPException(status_code=401, detail="Sign in to see your own settings.")
    return person


def _options() -> Dict[str, Any]:
    from core import voice
    from core.lang import LANG_NAMES
    label = {"friendly": "Friendly", "fun": "Fun", "formal": "Formal", "direct": "Straight to the point",
             "short": "Short", "detailed": "Detailed"}
    return {
        "style": [{"id": k, "label": label[k]} for k in voice.STYLES],
        "reply_length": [{"id": k, "label": label[k]} for k in voice.LENGTHS if k != "medium"],
        "language": [{"id": code, "label": name} for code, name in LANG_NAMES.items()],
    }


class PrefsPatch(BaseModel):
    style: Optional[str] = None
    reply_length: Optional[str] = None
    language: Optional[str] = None
    nickname: Optional[str] = None


@router.get("/{tenant_id}/admin/me/prefs")
async def get_my_prefs(tenant_id: str, request: Request) -> dict:
    """My own style choices. `known` is false for a login that isn't tied to a team member —
    there is nowhere to keep the choices, so the dashboard doesn't offer them."""
    from core import voice
    person = await _me(tenant_id, request)
    return {"known": bool(person.member_id), "name": person.name,
            "prefs": voice.clean_prefs(person.prefs), "options": _options()}


@router.patch("/{tenant_id}/admin/me/prefs")
async def set_my_prefs(tenant_id: str, body: PrefsPatch, request: Request) -> dict:
    """Save my own style choices. Only the fixed choices are kept (core/voice.py::clean_prefs):
    a value that isn't one of them is dropped, so nothing typed here can become an instruction
    to the assistant. An empty value clears that choice."""
    from core import voice
    from vula import team_index
    from vula.commerce import service
    person = await _me(tenant_id, request)
    if not person.member_id:
        raise HTTPException(status_code=409, detail=(
            "Your login isn't linked to a team member yet. Ask the owner to add you under Team."))
    prefs = voice.clean_prefs(body.model_dump())
    try:
        res = (service._client().table("vula_team_members").update({"prefs": prefs})
               .eq("tenant_id", tenant_id).eq("id", person.member_id).execute())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Couldn't save that ({exc}). Run migration 257?")
    # Read back what was stored, rather than report what was sent.
    rows = getattr(res, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Your team record wasn't found.")
    team_index.invalidate(tenant_id)
    saved = rows[0].get("prefs") if isinstance(rows[0], dict) else None
    return {"ok": True, "prefs": voice.clean_prefs(saved if isinstance(saved, dict) else prefs)}
