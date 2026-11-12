"""vula/api/me.py — what belongs to the signed-in person, not to the business.

Everything here acts on the CALLER, taken from their verified login (vula/identity.py). There is
no member id in any path or body: nobody — not a colleague, not the owner — can read or change
another person's settings through these routes.

Mounted under /v1/commerce, so the paths are /v1/commerce/{tenant_id}/admin/me/… and pass the
same membership guard as the rest of the tenant admin API.

  GET/PATCH  /{tenant_id}/admin/me/prefs          how Vula talks to me (core/voice.py)
  GET/POST   /{tenant_id}/admin/me/memory         what Vula remembers about me (vula/personal_memory.py)
  DELETE     /{tenant_id}/admin/me/memory[/{id}]  forget one note, or all of them

Also here, because they are small and are the owner's own view of their workspace:
  GET        /{tenant_id}/admin/quality            how well Vula is answering (vula/quality.py)
  GET        /{tenant_id}/admin/setup/owner        what is left to set up (vula/commerce/owner_setup.py)
  GET        /{tenant_id}/admin/plan               the plan, the trial, and the link to pay
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


# ── what Vula remembers about me (vula/personal_memory.py) ───────────────────────────────────

class NoteIn(BaseModel):
    text: str


def _memory_on() -> None:
    from vula import personal_memory
    if not personal_memory.enabled():
        raise HTTPException(status_code=404, detail="Private notes aren't switched on for this workspace.")


@router.get("/{tenant_id}/admin/me/memory")
async def get_my_memory(tenant_id: str, request: Request) -> dict:
    """Everything Vula is keeping about me. Only ever the caller's own notes."""
    import asyncio
    from vula import personal_memory
    person = await _me(tenant_id, request)
    if not personal_memory.enabled():
        return {"enabled": False, "notes": []}
    try:
        notes = await asyncio.to_thread(personal_memory.notes, person)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Couldn't read your notes ({exc}). Run migration 259?")
    return {"enabled": True, "limit": personal_memory.MAX_NOTES, "notes": notes}


@router.post("/{tenant_id}/admin/me/memory")
async def add_my_memory(tenant_id: str, body: NoteIn, request: Request) -> dict:
    """Ask Vula to remember something about me. Typing it here is the consent."""
    import asyncio
    from vula import personal_memory
    _memory_on()
    person = await _me(tenant_id, request)
    try:
        out = await asyncio.to_thread(personal_memory.remember, person, body.text, "dashboard")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Couldn't save that ({exc}). Run migration 259?")
    if out.get("saved"):
        return {"ok": True, "saved": out["saved"]}
    detail = {"empty": "Write what you'd like me to remember.",
              "full": f"I'm already keeping {personal_memory.MAX_NOTES} things about you. Remove one first."}
    raise HTTPException(status_code=400 if out.get("error") in detail else 503,
                        detail=detail.get(out.get("error"), "That wasn't saved. Please try again."))


@router.delete("/{tenant_id}/admin/me/memory/{note_id}")
async def forget_my_note(tenant_id: str, note_id: str, request: Request) -> dict:
    """Forget one of my notes. Someone else's note id is simply not found."""
    import asyncio
    from vula import personal_memory
    _memory_on()
    person = await _me(tenant_id, request)
    if not await asyncio.to_thread(personal_memory.forget, person, note_id):
        raise HTTPException(status_code=404, detail="That note wasn't found.")
    return {"ok": True}


@router.delete("/{tenant_id}/admin/me/memory")
async def forget_all_my_memory(tenant_id: str, request: Request) -> dict:
    """Forget everything Vula is keeping about me."""
    import asyncio
    from vula import personal_memory
    _memory_on()
    person = await _me(tenant_id, request)
    removed = await asyncio.to_thread(personal_memory.forget_all, person)
    return {"ok": True, "removed": removed}


# ── how well Vula is answering (vula/quality.py) ─────────────────────────────────────────────

@router.get("/{tenant_id}/admin/quality")
async def answer_quality(tenant_id: str, days: int = 7) -> dict:
    """Four numbers for the last `days` days: answers backed by the business's own records,
    answers Vula was unsure of, replies marked wrong, and questions handed to a person. Counts
    only — nothing anyone wrote is returned."""
    import asyncio
    from vula import quality
    return await asyncio.to_thread(quality.weekly, tenant_id, max(1, min(int(days), 90)))


# ── getting set up, and the plan (vula/commerce/owner_setup.py) ──────────────────────────────

@router.get("/{tenant_id}/admin/setup/owner")
async def owner_setup(tenant_id: str) -> dict:
    """What is left to set up, in the owner's words, with WhatsApp as an optional step. Built
    from the same live checklist the operator sees."""
    import asyncio
    from vula.api.master import setup_checklist
    from vula.commerce.owner_setup import owner_view
    return owner_view(await asyncio.to_thread(setup_checklist, tenant_id))


@router.get("/{tenant_id}/admin/plan")
async def my_plan(tenant_id: str, request: Request) -> dict:
    """This business's plan, trial and limits. The link to pay is included only for the owner
    or a manager — a restricted staff login sees the plan, not a way to commit the business."""
    import asyncio
    from vula.api.tenant_auth import member_scope, scope_allows
    from vula.commerce.owner_setup import plan_view
    scope = await member_scope(request.headers.get("authorization", ""), tenant_id)
    can_pay = scope is not None and scope_allows(scope, frozenset())
    return await asyncio.to_thread(lambda: plan_view(tenant_id, with_pay_link=can_pay))
