"""
vula/commerce/project_admin.py — the owner managing the project register on WhatsApp.

2026-10-02 (Ian: "how can a tenant add projects and aliases, phases …"). The dashboard's
Projects screen does all of this; these are the same actions from a WhatsApp message, matched
deterministically (no model) and answered with what was actually done:

    "Add phase 2 to Sporty TV"                     → a phase (child project) of Sporty TV
    "Sporty phase 2 is also called Sporty TV"      → an alias on the registered project
    "Sporty – Phase 2 is part of Sporty TV"        → the old name moves into a new phase
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

_ADD_PHASE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:add|create|start|open)\s+(?:a\s+)?(?:new\s+)?(?P<phase>(?:phase|stage)\s*[\w-]+|"
    r"phase\s+called\s+[^,]+?|[^,]{2,40}?\s+phase)\s+(?:to|for|on|under)\s+(?:the\s+)?(?P<project>.{2,60}?)"
    r"(?:\s+project)?\s*[.!]*\s*$", re.IGNORECASE)
_ALIAS_RE = re.compile(
    r"^\s*(?P<alias>.{2,60}?)\s+is\s+(?:also\s+(?:called|known\s+as)|the\s+same\s+(?:as|project\s+as)|"
    r"another\s+name\s+for)\s+(?P<project>.{2,60}?)\s*[.!]*\s*$", re.IGNORECASE)
_PART_OF_RE = re.compile(
    r"^\s*(?P<old>.{2,60}?)\s+is\s+(?:part\s+of|(?P<ph>phase\s*\w+|stage\s*\w+)\s+of)\s+(?:the\s+)?"
    r"(?P<project>.{2,60}?)(?:\s+project)?\s*[.!]*\s*$", re.IGNORECASE)


def _registered(tenant_id: str, name: str) -> Optional[dict]:
    from vula.commerce.service import canonical_project, project_key, registered_projects
    canon = canonical_project(tenant_id, name) or name
    for r in registered_projects(tenant_id):
        if project_key(r.get("name")) == project_key(canon):
            return r
    return None


def _phase_label(text: str) -> str:
    t = re.sub(r"^phase\s+called\s+", "", text.strip(), flags=re.IGNORECASE)
    return t[:1].upper() + t[1:]


async def handle(tenant_id: str, text: str) -> Optional[str]:
    """The reply when `text` is one of the register commands, else None."""
    from fastapi import HTTPException
    from vula.api import projects as api
    try:
        m = _PART_OF_RE.match(text or "")
        if m:
            proj = _registered(tenant_id, m.group("project"))
            if not proj:
                return f"I don't have a project called {m.group('project').strip()} — add it in Projects first."
            old = m.group("old").strip()
            ph = m.group("ph")
            if not ph:                     # "Sporty – Phase 2 is part of Sporty TV" → "Phase 2"
                found = re.search(r"(phase|stage)\s*\w+", old, re.I)
                ph = found.group(0) if found else "Phase 2"
            phase = _phase_label(ph)
            res = await api.add_phase(tenant_id, proj["id"], api.PhaseIn(phase=phase, move_from=old))
            if res.get("error"):
                return f"I couldn't add that phase: {res['error']}"
            n = (res.get("moved") or {}).get("vula_filed_documents", 0)
            return (f"✅ {res['name']} is now a phase of {proj['name']}, with its own BOQ, costs and documents."
                    + (f" {n} document(s) filed under “{old}” moved into it." if n else ""))
        m = _ADD_PHASE_RE.match(text or "")
        if m:
            proj = _registered(tenant_id, m.group("project"))
            if not proj:
                return None            # not a project we know — let the rest of Vula answer
            res = await api.add_phase(tenant_id, proj["id"], api.PhaseIn(phase=_phase_label(m.group("phase"))))
            if res.get("error"):
                return f"I couldn't add that phase: {res['error']}"
            return f"✅ Added {res['name']} — its own BOQ, budget and documents, rolled up under {proj['name']}."
        m = _ALIAS_RE.match(text or "")
        if m:
            a, b = m.group("alias").strip(), m.group("project").strip()
            proj, alias = _registered(tenant_id, b), a
            if not proj:
                proj, alias = _registered(tenant_id, a), b
            if not proj:
                return None
            res = await api.add_alias(tenant_id, proj["id"], api.AliasIn(alias=alias))
            n = (res.get("moved") or {}).get("vula_filed_documents", 0)
            return (f"✅ “{alias}” now means {proj['name']}."
                    + (f" {n} document(s) filed under that name moved onto {proj['name']}." if n else ""))
    except HTTPException as exc:
        return f"I couldn't do that: {exc.detail}"
    except Exception as exc:  # noqa: BLE001
        log.warning("project register command failed: %s", exc)
        return None
    return None
