"""
vula/integrations/project_context.py — make a chat project-aware.

When a WhatsApp message references a known project, build a compact context block
(client, status, the applicable standards from the project's code library, and the
professional team) so the assistant answers in that project's context and knows
which codes apply. Stateless + best-effort — never blocks a reply.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 4}


def detect_project(tenant_id: str, text: str) -> dict | None:
    """Return the project row whose name a message references, else None."""
    try:
        projects = (_client().table("vula_projects")
                    .select("id,name,number,client,status")
                    .eq("tenant_id", tenant_id).limit(200).execute().data or [])
    except Exception:
        return None
    if not projects:
        return None
    tt = _tokens(text)
    if not tt:
        return None
    best, best_score = None, 0
    for p in projects:
        # distinctive tokens of the project name/number
        name_tokens = _tokens(p.get("name", "")) | _tokens(p.get("number", "") or "")
        score = len(name_tokens & tt)
        if score > best_score:
            best, best_score = p, score
    return best if best_score else None


def project_context_block(tenant_id: str, text: str) -> str:
    """A compact, prompt-ready context block for the project referenced in `text`,
    or '' if none is detected. Includes linked standards + team so the assistant
    scopes its answer and cites the project's applicable codes."""
    proj = detect_project(tenant_id, text)
    if not proj:
        return ""
    c = _client()
    codes, team = [], []
    try:
        links = (c.table("vula_project_codes").select("code_id")
                 .eq("project_id", proj["id"]).execute().data or [])
        cids = [l["code_id"] for l in links]
        if cids:
            codes = (c.table("vula_code_library").select("code_ref,title,version,status")
                     .in_("id", cids).execute().data or [])
        team = (c.table("vula_project_team").select("name,role")
                .eq("project_id", proj["id"]).execute().data or [])
    except Exception as exc:
        logger.debug("project context fetch failed: %s", exc)

    lines = [f"[Active project: {proj['name']}"
             + (f" ({proj['number']})" if proj.get("number") else "") + "]"]
    if proj.get("client"):
        lines.append(f"Client: {proj['client']} · Status: {proj.get('status','active')}")
    if codes:
        cur = [f"{x['code_ref']} — {x['title']}" + (f" ({x['version']})" if x.get("version") else "")
               for x in codes if x.get("status") != "superseded"]
        if cur:
            lines.append("Applicable standards for this project (use these; say so if a needed "
                         "one isn't listed): " + "; ".join(cur))
    if team:
        lines.append("Professional team: " + ", ".join(
            f"{m['name']}" + (f" ({m['role']})" if m.get("role") else "") for m in team))
    return "\n".join(lines)
