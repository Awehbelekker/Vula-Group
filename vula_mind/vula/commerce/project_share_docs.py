"""vula/commerce/project_share_docs.py — a project's documents on the client's link: view, ask, download.

3 Nov 2026 (Ian): "We want to allow the client to view, but they can request if they want to
download." The client's project link (/track/{token}, vula/api/project_track.py) lists the
project's client documents — the same set as the project file's client copy
(vula/commerce/project_file.py: supplier invoices, quotes, drawings/BOQ/specs; never statements,
proofs of payment, contracts or internal reports).

  view      each page is shown as a picture with "View only" across it — no download button, no
            original file. (A determined person can still screenshot a page; this stops the file
            itself being handed out, which is what the business asked for.)
  request   the client asks to download one document or all of them; the owner and managers get a
            WhatsApp question (yes / no) and see it in the dashboard (Projects › the project › Money).
  download  only while an approval for that link is current (APPROVAL_DAYS): the original file,
            or the whole client copy as a zip with its checked index.

Everything is keyed to the link (vula_project_shares) and checked against its business and
project — a document id from another project or business is never shown or served.
Migration 243 (vula_project_share_requests).
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_project_share_requests"
APPROVAL_DAYS = 7
MAX_OPEN = 20                 # open requests per link — a link can't flood the owner
MAX_PAGES = 40                # pages shown per document
VIEWABLE = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


def _client():
    from vula.commerce.service import _client as c
    return c()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── which documents ─────────────────────────────────────────────────────────

def client_documents(tenant_id: str, project: str, business: str = "") -> List[Dict[str, Any]]:
    """The project's client documents, in the project file's order (tenant-scoped)."""
    from vula.commerce import project_file as pf
    sup = pf._Suppliers(pf._supplier_rows(tenant_id), business)
    rows = pf.organise(pf._documents(tenant_id, project), sup)["rows"]
    return [r for r in rows if r["client"]]


def find(tenant_id: str, project: str, doc_id: str, business: str = "") -> Optional[Dict[str, Any]]:
    """One of the project's client documents by id, or None — never anything else."""
    return next((r for r in client_documents(tenant_id, project, business) if str(r["id"]) == str(doc_id)), None)


def label(row: Dict[str, Any]) -> str:
    bits = [row.get("supplier") if row.get("by_supplier") else row.get("category"),
            row.get("number") or "", row.get("date") or ""]
    return " · ".join(b for b in bits if b) or (row.get("filename") or "Document")


# ── view only ───────────────────────────────────────────────────────────────

def _files(row: Dict[str, Any]) -> List[tuple]:
    return [(u, e) for u, e in [(row.get("file_url"), row.get("ext") or "")] +
            [(p.get("file_url"), p.get("ext") or "") for p in row.get("pages") or []] if u]


def viewable(row: Dict[str, Any]) -> bool:
    return bool(_files(row)) and all((e or "").lower() in VIEWABLE for _u, e in _files(row))


def _pdf_pages(data: bytes) -> int:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def page_count(row: Dict[str, Any], fetch: Callable[[str], bytes]) -> int:
    n = 0
    for url, ext in _files(row):
        n += _pdf_pages(fetch(url)) if ext.lower() == ".pdf" else 1
    return min(n, MAX_PAGES)


def _font(size: int):
    from PIL import ImageFont
    for path in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def watermark(img, text: str):
    """`text` across the page, repeated on a slant, light enough to read the document through."""
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    w, h = img.size
    layer = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(max(14, w // 28))
    step_y = max(60, h // 7)
    for i, y in enumerate(range(0, h * 2, step_y)):
        draw.text((((i % 2) * w // 6) - w // 4, y), (text + "     ") * 4, font=font, fill=(150, 30, 30, 60))
    layer = layer.rotate(25, expand=False)
    box = ((layer.width - w) // 2, (layer.height - h) // 2)
    layer = layer.crop((box[0], box[1], box[0] + w, box[1] + h))
    out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    return out


def page_png(row: Dict[str, Any], n: int, fetch: Callable[[str], bytes], mark: str) -> bytes:
    """Page `n` (1-based, across the document's files) as a watermarked PNG. IndexError past the end."""
    from PIL import Image
    if n < 1 or n > MAX_PAGES:
        raise IndexError(n)
    left = n
    for url, ext in _files(row):
        data = fetch(url)
        if ext.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if left <= doc.page_count:
                    pix = doc[left - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    break
                left -= doc.page_count
        else:
            if left == 1:
                img = Image.open(io.BytesIO(data))
                img.thumbnail((1600, 2200))
                break
            left -= 1
    else:
        raise IndexError(n)
    buf = io.BytesIO()
    watermark(img, mark).save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ── requests and approvals ─────────────────────────────────────────────────

def requests_for_share(share_id: str) -> List[Dict[str, Any]]:
    return (_client().table(TABLE).select("*").eq("share_id", share_id).limit(200).execute().data or [])


def access(share: Dict[str, Any]) -> Dict[str, Any]:
    """What this link may download now, and what it has asked for: {all, docs, asked}."""
    now = _now().isoformat()
    out: Dict[str, Any] = {"all": False, "docs": set(), "asked": set(), "until": None}
    try:
        rows = requests_for_share(share["id"])
    except Exception as exc:  # noqa: BLE001 — before migration 243: view only, nothing to download
        log.debug("share requests unavailable: %s", exc)
        return out
    for r in rows:
        if r.get("tenant_id") != share.get("tenant_id"):
            continue
        if r.get("status") == "approved" and str(r.get("approved_until") or "") > now:
            if r["scope"] == "all":
                out["all"] = True
            else:
                out["docs"].add(r["scope"])
            out["until"] = max(filter(None, [out["until"], str(r["approved_until"])[:10]]))
        elif r.get("status") == "requested":
            out["asked"].add(r["scope"])
    return out


def may_download(share: Dict[str, Any], doc_id: Optional[str] = None) -> bool:
    a = access(share)
    return a["all"] or (doc_id is not None and str(doc_id) in a["docs"])


def ask(share: Dict[str, Any], scope: str, business: str = "") -> Dict[str, Any]:
    """The client asks to download `scope` ('all' or a client document id). Returns the request row
    and whether it is new (an open request for the same thing is returned, not repeated).
    ValueError for a scope that isn't this project's client document."""
    tenant_id, project = share["tenant_id"], share["project"]
    if scope != "all" and not find(tenant_id, project, scope, business):
        raise ValueError("unknown document")
    rows = requests_for_share(share["id"])
    open_rows = [r for r in rows if r.get("status") == "requested"]
    same = next((r for r in open_rows if r.get("scope") == scope), None)
    if same:
        return {"request": same, "new": False}
    if len(open_rows) >= MAX_OPEN:
        raise ValueError("too many open requests")
    db = _client()
    res = db.table(TABLE).insert({"tenant_id": tenant_id, "share_id": share["id"], "project": project,
                                  "scope": scope, "status": "requested"}).execute()
    row = ((getattr(res, "data", None) or [None])[0])
    back = [r for r in requests_for_share(share["id"]) if r.get("status") == "requested" and r.get("scope") == scope]
    if not back:
        raise RuntimeError("the request was not saved")
    return {"request": row or back[-1], "new": True}


def decide(tenant_id: str, request_id: str, approve: bool, by: Optional[str] = None) -> Dict[str, Any]:
    """The owner's yes / no. Approval opens downloads for APPROVAL_DAYS. Read back."""
    now = _now()
    patch = {"status": "approved" if approve else "declined", "decided_at": now.isoformat(), "decided_by": by,
             "approved_until": (now + timedelta(days=APPROVAL_DAYS)).isoformat() if approve else None}
    db = _client()
    db.table(TABLE).update(patch).eq("tenant_id", tenant_id).eq("id", request_id).eq("status", "requested").execute()
    rows = (db.table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", request_id).limit(1).execute().data or [])
    if not rows or rows[0].get("status") != patch["status"]:
        raise RuntimeError("the answer was not saved (already decided?)")
    return rows[0]


def list_requests(tenant_id: str, project: Optional[str] = None, business: str = "") -> List[Dict[str, Any]]:
    """The project's download requests, newest first, with what each asked for (dashboard)."""
    from vula.commerce.service import project_key
    try:
        rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).limit(500).execute().data or [])
    except Exception:
        return []
    rows = [r for r in rows if not project or project_key(r.get("project")) == project_key(project)]
    docs = {}
    if project and any(r.get("scope") != "all" for r in rows):
        docs = {str(d["id"]): d for d in client_documents(tenant_id, project, business)}
    for r in rows:
        r["what"] = "all documents" if r.get("scope") == "all" else label(docs.get(str(r.get("scope")), {}))
    return sorted(rows, key=lambda r: str(r.get("requested_at") or ""), reverse=True)


def prompt(project: str, view_label: str, what: str) -> str:
    return (f"📥 The *{project}* client link ({view_label}) asks to download {what}.\n"
            f"Reply *yes* to allow downloads on that link for {APPROVAL_DAYS} days, or *no*. "
            "You can also answer in the dashboard: Projects › the project › Money.")
