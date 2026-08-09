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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["projects"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


# ── Project finances (money in/out, budget-vs-actual) ─────────────────────────

@router.get("/{tenant}/finances")
async def get_finances(tenant: str, project: Optional[str] = None) -> dict:
    from vula.integrations.finances import finance_summary
    return finance_summary(tenant, project)


@router.get("/{tenant}/p/{project}/financials")
async def get_project_financials(tenant: str, project: str) -> dict:
    """Unified money picture for one project: contract/budget, invoiced, paid-in, spent, net."""
    from vula.integrations.finances import project_financials
    return project_financials(tenant, project)


class BoqIn(BaseModel):
    total: float                 # BoQ / contract value in Rands
    title: Optional[str] = None
    source_job: Optional[str] = None


@router.post("/{tenant}/p/{project}/boq")
async def set_project_boq(tenant: str, project: str, body: BoqIn) -> dict:
    """Persist a project's BoQ / contract value (e.g. from a takeoff) so it survives restarts
    and drives the unified project financials' contract figure."""
    row = {"tenant_id": tenant, "project": project, "title": body.title,
           "total_cents": int(round(body.total * 100)), "source_job": body.source_job,
           "updated_at": "now()"}
    try:
        _client().table("vula_project_boq").upsert(row, on_conflict="tenant_id,project").execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 056?)"}
    return {"project": project, "contract": body.total}


class BudgetIn(BaseModel):
    project: str
    budget: float


@router.post("/{tenant}/finances/backfill")
async def backfill_finances(tenant: str) -> dict:
    """Re-post already-filed invoices/payments (with an amount) into the ledger.
    Idempotent — useful for docs filed before the ledger existed."""
    from vula.integrations.finances import post_finance_from_doc
    try:
        docs = (_client().table("vula_filed_documents").select("project,fields,doc_id,filename,summary,category")
                .eq("tenant_id", tenant).eq("status", "filed").execute().data or [])
    except Exception as exc:
        return {"error": str(exc)}
    posted = 0
    for d in docs:
        f = d.get("fields") or {}
        if not f.get("amount"):
            continue
        row = post_finance_from_doc(tenant, d.get("project"), f, d.get("doc_id"),
                                    d.get("filename") or "", d.get("summary") or "", d.get("category") or "")
        if row:
            posted += 1
    return {"tenant": tenant, "scanned": len(docs), "posted": posted}


@router.post("/{tenant}/budget")
async def set_budget(tenant: str, body: BudgetIn) -> dict:
    try:
        _client().table("vula_project_budgets").upsert(
            {"tenant_id": tenant, "project": body.project, "budget": body.budget,
             "updated_at": "now()"}, on_conflict="tenant_id,project").execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 027?)"}
    return {"project": body.project, "budget": body.budget}


# ── Progress claims / interim payment certificates (structured, JBCC-style) ───

class ClaimIn(BaseModel):
    cumulative_value: float          # Rands — QS-assessed value of ALL work done to date
    retention_pct: float = 5.0
    claim_date: Optional[str] = None
    notes: Optional[str] = None


@router.get("/{tenant}/p/{project}/claims")
async def list_project_claims(tenant: str, project: str) -> dict:
    from vula.integrations.progress_claims import list_claims
    return {"claims": list_claims(tenant, project)}


@router.post("/{tenant}/p/{project}/claims")
async def create_project_claim(tenant: str, project: str, body: ClaimIn) -> dict:
    from vula.integrations.progress_claims import create_claim
    try:
        claim = create_claim(tenant, project, int(round(body.cumulative_value * 100)),
                             retention_pct=body.retention_pct, claim_date=body.claim_date,
                             notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        return {"error": f"{exc} (run migration 125?)"}
    return {"claim": claim}


@router.post("/{tenant}/p/{project}/claims/{claim_id}/certify")
async def certify_project_claim(tenant: str, project: str, claim_id: str) -> dict:
    from vula.integrations.progress_claims import certify_claim
    try:
        claim = certify_claim(tenant, claim_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"claim": claim}


class ClaimInvoiceIn(BaseModel):
    customer_name: str
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_address: Optional[str] = None
    vat_rate: float = 15.0


@router.post("/{tenant}/p/{project}/claims/{claim_id}/invoice")
async def invoice_project_claim(tenant: str, project: str, claim_id: str, body: ClaimInvoiceIn) -> dict:
    from vula.integrations.progress_claims import convert_claim_to_invoice
    try:
        invoice = await convert_claim_to_invoice(tenant, claim_id, {
            "name": body.customer_name, "phone": body.customer_phone,
            "email": body.customer_email, "address": body.customer_address,
        }, vat_rate=body.vat_rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"invoice": invoice}


# ── Project Workspaces ("Claude Projects" for Vula) ───────────────────────────

@router.get("/{tenant}/labels")
async def project_labels(tenant: str) -> dict:
    """Distinct project labels a workspace can open (from filed docs, finances, threads)."""
    labels = set()
    db = _client()
    for table, col in (("vula_filed_documents", "project"), ("vula_project_finances", "project"),
                       ("vula_project_threads", "project")):
        try:
            for r in (db.table(table).select(col).eq("tenant_id", tenant).limit(2000).execute().data or []):
                if r.get(col):
                    labels.add(r[col])
        except Exception:
            pass
    try:
        from vula.integrations.doc_filing import project_examples
        labels.update(project_examples(tenant, n=20))
    except Exception:
        pass
    return {"projects": sorted(labels)}




@router.get("/{tenant}/p/{project}/threads")
async def list_threads(tenant: str, project: str) -> dict:
    try:
        rows = (_client().table("vula_project_threads").select("*")
                .eq("tenant_id", tenant).eq("project", project)
                .order("updated_at", desc=True).limit(100).execute().data or [])
    except Exception:
        rows = []
    return {"threads": rows}


@router.post("/{tenant}/p/{project}/threads")
async def new_thread(tenant: str, project: str) -> dict:
    try:
        res = _client().table("vula_project_threads").insert(
            {"tenant_id": tenant, "project": project, "title": "New chat"}).execute()
        return {"thread": (res.data or [{}])[0]}
    except Exception as exc:
        return {"error": f"{exc} (run migration 030?)"}


@router.get("/{tenant}/threads/{thread_id}/messages")
async def thread_messages(tenant: str, thread_id: str) -> dict:
    try:
        rows = (_client().table("vula_project_messages").select("role,content,created_at")
                .eq("tenant_id", tenant).eq("thread_id", thread_id)
                .order("created_at").limit(200).execute().data or [])
    except Exception:
        rows = []
    return {"messages": rows}


class ChatIn(BaseModel):
    project: str
    message: str


@router.post("/{tenant}/threads/{thread_id}/messages")
async def thread_chat(tenant: str, thread_id: str, body: ChatIn) -> dict:
    from vula.integrations.workspace import workspace_chat
    reply = await workspace_chat(tenant, body.project, thread_id, body.message)
    return {"reply": reply}


@router.get("/{tenant}/p/{project}/brief")
async def get_brief(tenant: str, project: str) -> dict:
    try:
        rows = (_client().table("vula_project_briefs").select("brief")
                .eq("tenant_id", tenant).eq("project", project).limit(1).execute().data or [])
    except Exception:
        rows = []
    return {"project": project, "brief": rows[0]["brief"] if rows else ""}


class BriefIn(BaseModel):
    brief: str


@router.put("/{tenant}/p/{project}/brief")
async def set_brief(tenant: str, project: str, body: BriefIn) -> dict:
    try:
        _client().table("vula_project_briefs").upsert(
            {"tenant_id": tenant, "project": project, "brief": body.brief, "updated_at": "now()"},
            on_conflict="tenant_id,project").execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 030?)"}
    return {"project": project, "brief": body.brief}


@router.get("/{tenant}/p/{project}/tasks")
async def list_tasks(tenant: str, project: str) -> dict:
    try:
        rows = (_client().table("vula_project_tasks").select("*")
                .eq("tenant_id", tenant).eq("project", project)
                .order("status").order("created_at", desc=True).limit(200).execute().data or [])
    except Exception:
        rows = []
    return {"tasks": rows}


class TaskIn(BaseModel):
    title: str
    assignee: Optional[str] = None
    due: Optional[str] = None


@router.post("/{tenant}/p/{project}/tasks")
async def add_task(tenant: str, project: str, body: TaskIn) -> dict:
    row = {"tenant_id": tenant, "project": project, "title": body.title,
           "assignee": body.assignee, "due": body.due, "status": "open", "source": "vula"}
    try:
        res = _client().table("vula_project_tasks").insert(row).execute()
        task = (res.data or [row])[0]
    except Exception as exc:
        return {"error": f"{exc} (run migration 030?)"}
    # Mirror to ClickUp if this project maps to a list.
    try:
        from vula.integrations import workspace_clickup
        cid = await workspace_clickup.create_task(tenant, project, body.title)
        if cid:
            _client().table("vula_project_tasks").update({"clickup_task_id": cid}).eq("id", task["id"]).execute()
            task["clickup_task_id"] = cid
    except Exception as exc:
        log.debug("clickup task mirror skipped: %s", exc)
    return {"task": task}


class TaskPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None


@router.patch("/{tenant}/tasks/{task_id}")
async def update_task(tenant: str, task_id: str, body: TaskPatch) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    patch["updated_at"] = "now()"
    try:
        _client().table("vula_project_tasks").update(patch).eq("id", task_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    # Mirror a status change to ClickUp if linked.
    if body.status:
        try:
            row = (_client().table("vula_project_tasks").select("clickup_task_id")
                   .eq("id", task_id).limit(1).execute().data or [{}])[0]
            if row.get("clickup_task_id"):
                from vula.integrations import workspace_clickup
                await workspace_clickup.set_status(tenant, row["clickup_task_id"], body.status == "done")
        except Exception as exc:
            log.debug("clickup status mirror skipped: %s", exc)
    return {"id": task_id, **patch}


# ── Project board (Milanote-style cards, migration 089) ────────────────────────
# A board is implicitly (tenant_id, project) — same simplification as brief/tasks
# above. Cards are freely positioned (x/y/width/height) and hold loose JSONB
# content shaped by card_type: note {text}, image {url,caption}, link {url,title},
# checklist {items:[{text,done}]}.

@router.get("/{tenant}/p/{project}/board")
async def list_board_cards(tenant: str, project: str) -> dict:
    try:
        rows = (_client().table("vula_project_cards").select("*")
                .eq("tenant_id", tenant).eq("project", project)
                .order("z_index").order("created_at").limit(500).execute().data or [])
    except Exception as exc:
        log.debug("board list skipped (run migration 089?): %s", exc)
        rows = []
    return {"cards": rows}


class CardIn(BaseModel):
    card_type: str
    x: float = 40
    y: float = 40
    width: float = 220
    height: float = 160
    z_index: int = 1
    color: Optional[str] = None
    content: dict = {}
    created_by: Optional[str] = None


@router.post("/{tenant}/p/{project}/board")
async def add_board_card(tenant: str, project: str, body: CardIn) -> dict:
    if body.card_type not in ("note", "image", "link", "checklist"):
        return {"error": "card_type must be one of: note, image, link, checklist"}
    row = {"tenant_id": tenant, "project": project, **body.model_dump()}
    try:
        res = _client().table("vula_project_cards").insert(row).execute()
        return {"card": (res.data or [row])[0]}
    except Exception as exc:
        return {"error": f"{exc} (run migration 089?)"}


class CardPatch(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    z_index: Optional[int] = None
    color: Optional[str] = None
    content: Optional[dict] = None


@router.patch("/{tenant}/board/{card_id}")
async def update_board_card(tenant: str, card_id: str, body: CardPatch) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"error": "nothing to update"}
    patch["updated_at"] = "now()"
    try:
        _client().table("vula_project_cards").update(patch).eq("id", card_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"id": card_id, **patch}


@router.delete("/{tenant}/board/{card_id}")
async def delete_board_card(tenant: str, card_id: str) -> dict:
    try:
        _client().table("vula_project_cards").delete().eq("id", card_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"id": card_id, "deleted": True}


class FetchTitleIn(BaseModel):
    url: str


@router.post("/{tenant}/board/fetch-title")
async def fetch_link_title(tenant: str, body: FetchTitleIn) -> dict:
    """Best-effort page-title lookup for a link card, so the user doesn't have to type one."""
    import html as _html
    import re as _re
    import httpx
    url = (body.url or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _re.match(r"^https?://", url, _re.I):
        url = f"https://{url}"
    ua = "VulaCommerce/1.0 (link preview; contact: awehbelekker@gmail.com)"
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": ua}, follow_redirects=True) as client:
            resp = await client.get(url)
        m = _re.search(r"<title[^>]*>(.*?)</title>", resp.text, _re.I | _re.S)
        title = _html.unescape(_re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
        return {"title": title[:200], "url": url}
    except Exception as exc:
        log.debug("link title fetch failed for %r: %s", url, exc)
        return {"error": "Couldn't fetch that page's title", "url": url}


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
