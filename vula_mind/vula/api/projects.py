"""
vula/api/projects.py — Vula Projects, master code library, team directory.

Projects (Level 1), a master code library (standards uploaded once, linked to many
projects), and a professional-team directory with roles. Codes added with text are
ingested into the KB as authoritative `reference` content (retrievable, cited).

    GET/POST   /v1/projects/{tenant}                         projects
    GET/PATCH  /v1/projects/{tenant}/p/{project_id}          one project (+team +codes)
    GET/POST   /v1/projects/{tenant}/codes                   master code library
    POST/DEL   /v1/projects/{tenant}/p/{project_id}/codes/{code_id}   link/unlink
    GET/POST   /v1/projects/{tenant}/p/{project_id}/team     team members
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["projects"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectIn(BaseModel):
    name: str
    number: Optional[str] = None
    client: Optional[str] = None
    status: str = "active"
    created_by: Optional[str] = None


@router.get("/{tenant_id}")
async def list_projects(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_projects").select("*")
                .eq("tenant_id", tenant_id).order("created_at", desc=True)
                .limit(500).execute().data or [])
    except Exception as exc:
        log.warning("projects list failed (run migration 016?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "projects": rows, "count": len(rows)}


@router.post("/{tenant_id}")
async def create_project(tenant_id: str, body: ProjectIn) -> dict:
    row = {"tenant_id": tenant_id, **body.model_dump(exclude_none=True)}
    try:
        res = _client().table("vula_projects").insert(row).execute()
        return res.data[0] if res.data else {"error": "insert returned no row"}
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/{tenant_id}/p/{project_id}")
async def get_project(tenant_id: str, project_id: str) -> dict:
    c = _client()
    try:
        proj = (c.table("vula_projects").select("*").eq("id", project_id)
                .limit(1).execute().data or [])
        if not proj:
            return {"error": "Project not found."}
        team = (c.table("vula_project_team").select("*")
                .eq("project_id", project_id).execute().data or [])
        links = (c.table("vula_project_codes").select("code_id")
                 .eq("project_id", project_id).execute().data or [])
        code_ids = [l["code_id"] for l in links]
        codes = []
        if code_ids:
            codes = (c.table("vula_code_library").select("*")
                     .in_("id", code_ids).execute().data or [])
        return {**proj[0], "team": team, "codes": codes}
    except Exception as exc:
        return {"error": str(exc)}


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    number: Optional[str] = None
    client: Optional[str] = None
    status: Optional[str] = None


@router.patch("/{tenant_id}/p/{project_id}")
async def update_project(tenant_id: str, project_id: str, body: ProjectPatch) -> dict:
    patch = body.model_dump(exclude_none=True)
    if not patch:
        return {"error": "nothing to update"}
    try:
        _client().table("vula_projects").update(patch).eq("id", project_id).execute()
        return {"id": project_id, **patch}
    except Exception as exc:
        return {"error": str(exc)}


# ── Master code library ───────────────────────────────────────────────────────

class CodeIn(BaseModel):
    code_ref: str                       # STD-SANS10400-A
    title: str                          # SANS 10400 Part A: General Principles
    category: str = "Standards"
    version: Optional[str] = None
    status: str = "current"
    file_url: Optional[str] = None
    content: Optional[str] = None        # raw text → ingested into KB as reference
    uploaded_by: Optional[str] = None


@router.get("/{tenant_id}/codes")
async def list_codes(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_code_library").select("*")
                .eq("tenant_id", tenant_id).order("code_ref").limit(1000).execute().data or [])
    except Exception as exc:
        log.warning("code library list failed (run migration 016?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "codes": rows, "count": len(rows)}


@router.post("/{tenant_id}/codes")
async def add_code(tenant_id: str, body: CodeIn) -> dict:
    """Add a code to the master library; if `content` is given, ingest it into the
    KB as authoritative `reference` content (so it's retrievable + citable)."""
    doc_id = None
    if body.content and body.content.strip():
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            doc_id = f"code_{body.code_ref}"
            await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
                content=f"{body.title} ({body.code_ref})\n\n{body.content}",
                filename=f"{body.code_ref}.txt", doc_id=doc_id,
                source_type="reference",
            )
        except Exception as exc:
            log.warning("Code KB ingest failed for %s: %s", body.code_ref, exc)
            doc_id = None

    row = {
        "tenant_id": tenant_id, "code_ref": body.code_ref, "title": body.title,
        "category": body.category, "version": body.version, "status": body.status,
        "file_url": body.file_url, "doc_id": doc_id, "uploaded_by": body.uploaded_by,
    }
    try:
        res = (_client().table("vula_code_library")
               .upsert(row, on_conflict="tenant_id,code_ref").execute())
        return res.data[0] if res.data else {"error": "upsert returned no row"}
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/{tenant_id}/p/{project_id}/codes/{code_id}")
async def link_code(tenant_id: str, project_id: str, code_id: str) -> dict:
    try:
        _client().table("vula_project_codes").upsert(
            {"tenant_id": tenant_id, "project_id": project_id, "code_id": code_id},
            on_conflict="project_id,code_id").execute()
        return {"project_id": project_id, "code_id": code_id, "linked": True}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/{tenant_id}/p/{project_id}/codes/{code_id}")
async def unlink_code(tenant_id: str, project_id: str, code_id: str) -> dict:
    try:
        (_client().table("vula_project_codes").delete()
         .eq("project_id", project_id).eq("code_id", code_id).execute())
        return {"project_id": project_id, "code_id": code_id, "linked": False}
    except Exception as exc:
        return {"error": str(exc)}


# ── Professional team directory ───────────────────────────────────────────────

class TeamIn(BaseModel):
    name: str
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


@router.get("/{tenant_id}/p/{project_id}/team")
async def list_team(tenant_id: str, project_id: str) -> dict:
    try:
        rows = (_client().table("vula_project_team").select("*")
                .eq("project_id", project_id).execute().data or [])
    except Exception as exc:
        return {"error": str(exc), "team": []}
    return {"project_id": project_id, "team": rows}


@router.post("/{tenant_id}/p/{project_id}/team")
async def add_team_member(tenant_id: str, project_id: str, body: TeamIn) -> dict:
    row = {"tenant_id": tenant_id, "project_id": project_id, **body.model_dump(exclude_none=True)}
    try:
        res = _client().table("vula_project_team").insert(row).execute()
        return res.data[0] if res.data else {"error": "insert returned no row"}
    except Exception as exc:
        return {"error": str(exc)}
