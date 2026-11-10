"""
vula/api/project_track.py — the public project page a client or QS opens from a share link.

GET /track/{token} (no login — the token is the key; vula/commerce/project_tracker.py). An
unknown, revoked or expired link shows a plain "not valid" page; a live one shows the project in
the view the business chose for it, from the books as they are now. Its downloads are PDFs only
— the page itself, and the priced bill when the project has one priced online — never the
editable Excel (2026-10-27, Ian: "don't want people to reuse the template").
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

log = logging.getLogger(__name__)
router = APIRouter(tags=["project-track"])

_GONE = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
         "<h3 style='font-family:system-ui;margin:40px auto;max-width:440px'>This project link isn't valid "
         "any more. Please ask for a new one.</h3>")
_NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
_NOT_YET = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<h3 style='font-family:system-ui;margin:40px auto;max-width:440px'>This download needs the team's "
            "yes first. Go back to the project page and request it.</h3>")


def _wrong_address(share: dict, request: Optional[Request]) -> bool:
    """A link opened on another business's own address (projects.<their domain>) is not served there:
    each business's address only opens its own clients' links. Vula's own address opens any."""
    if request is None:
        return False
    from vula.commerce.web_basket import tenant_for_link_host, tenant_for_shop_host
    host = request.headers.get("host") or ""
    owner = tenant_for_link_host(host) or tenant_for_shop_host(host)
    return bool(owner and owner != share.get("tenant_id"))


async def _open(token: str, request: Optional[Request] = None):
    """(share, brand, client view, priced-bill lines) for a live link, else None."""
    from vula.api.commerce import public_brand
    from vula.commerce import boq_online, project_tracker as pt
    share = await asyncio.to_thread(pt.share_for_token, token)
    if not share or _wrong_address(share, request):
        return None
    pos = await asyncio.to_thread(pt.position, share["tenant_id"], share["project"])
    brand = await public_brand(share["tenant_id"])
    bill = await asyncio.to_thread(boq_online.load, share["tenant_id"], share["project"])
    return share, brand, pt.client_view(pos, share["view"]), bill.get("lines") or []


def _pdf(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf",
                    headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{name}.pdf"'})


def _name(view: dict) -> str:
    return "".join(ch for ch in str(view.get("project") or "Project") if ch.isalnum() or ch in " -_").strip().replace(" ", "_")


@router.get("/link-ping")
async def link_ping(request: Request):
    """Which business this address belongs to — how Vula knows a client-links address is live
    (web_basket.check_link_host), like /shop-ping for a shop's address."""
    from vula.commerce.web_basket import tenant_for_link_host
    return {"links": tenant_for_link_host(request.headers.get("host") or "")}


@router.get("/track/{token}", response_class=HTMLResponse)
async def project_page(token: str, request: Request, asked: str = ""):
    from vula.commerce import project_tracker as pt
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, view, lines = got
    links = {"Download this page (PDF)": f"/track/{token}/pdf"}
    if lines:
        links["Priced bill (PDF)"] = f"/track/{token}/bill.pdf"
    await asyncio.to_thread(pt.note_view, share)
    try:
        docs_html = await asyncio.to_thread(_documents_html, token, share, brand, asked)
    except Exception as exc:  # noqa: BLE001 — the account still shows; documents are extra
        log.warning("project link documents failed: %s", exc)
        docs_html = ""
    return HTMLResponse(pt.render(brand, view, links, docs_html), headers=_NO_STORE)


# ── the project's documents on the link: view only, download on the business's yes ──
# (vula/commerce/project_share_docs.py, migration 243)

def _documents_html(token: str, share: dict, brand: dict, asked: str = "") -> str:
    import html as _html
    from vula.commerce import project_share_docs as sd
    e = _html.escape
    docs = sd.client_documents(share["tenant_id"], share["project"], brand.get("name") or "")
    if not docs:
        return ""
    acc = sd.access(share)
    base = f"/track/{e(token)}"
    note = ""
    if asked:
        note = ("<p class='note'>Thanks — your request has gone to the team. Once they say yes, the "
                "download buttons appear here.</p>")
    if acc["all"]:
        top = (f"<p class='note'>Downloads are open until {e(acc['until'] or '')}. "
               f"<a class='btn solid' href='{base}/download.zip'>Download all (zip)</a></p>")
    elif "all" in acc["asked"]:
        top = "<p class='note'>You've asked to download all the documents — waiting for the team.</p>"
    else:
        top = (f"<form class='inline' method='post' action='{base}/request'><input type='hidden' name='scope' value='all'>"
               "<button class='btn' type='submit'>Request to download all</button></form>")
    # grouped by category (10 Nov 2026): the business's own documents first, then the project file's
    # folders in their order — invoices, quotes, drawings/BOQ/specifications — by supplier and date
    ours_label = f"From {brand.get('name')}" if brand.get("name") else "From us"
    groups: dict = {}
    for d in docs:
        ours = bool(d.get("by_supplier") and (d.get("supplier") or "").endswith("(our documents)"))
        groups.setdefault((0, ours_label) if ours else (1, d["folder"][3:]), []).append(d)
    blocks = []
    for (_rank, title), members in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[1][0]["folder"])):
        blocks.append(f"<h4 class='grp'>{e(title)} <span class='cnt'>({len(members)})</span></h4>"
                      f"<div class='docs'>{''.join(_doc_row(d, base, acc, brand, sd) for d in members)}</div>")
    return (f"<h3>Documents ({len(docs)})</h3>{note}<p class='muted'>View any document here. To keep a copy, "
            f"request a download — the team says yes or no.</p>{top}{''.join(blocks)}")


def _doc_row(d: dict, base: str, acc: dict, brand: dict, sd) -> str:
    """One document on the client's link: who, amount, number and date, and what they can do with it."""
    import html as _html
    e = _html.escape
    did = e(str(d["id"]))
    who = d["supplier"] if d.get("by_supplier") else d.get("category") or ""
    if who.endswith("(our documents)"):              # the business's own document — to the client, just its name
        who = brand.get("name") or "Our documents"
    who = e(who)
    sub = " · ".join(x for x in (d.get("number") or "", d.get("date") or "") if x)
    amount = ""
    if d.get("amount_cents") is not None and d["folder"] in ("01 Invoices", "02 Quotes"):
        c = d["amount_cents"]
        amount = ("-" if c < 0 else "") + f"R{abs(c) / 100:,.2f}".replace(",", " ")
    acts = []
    if sd.viewable(d):
        acts.append(f"<a class='btn' href='{base}/doc/{did}'>View</a>")
    if acc["all"] or str(d["id"]) in acc["docs"]:
        acts.append(f"<a class='btn solid' href='{base}/download/{did}'>Download</a>")
    elif str(d["id"]) in acc["asked"] or "all" in acc["asked"]:
        acts.append("<span class='d'>download requested</span>")
    else:
        acts.append(f"<form class='inline' method='post' action='{base}/request'>"
                    f"<input type='hidden' name='scope' value='{did}'>"
                    "<button class='btn' type='submit'>Request download</button></form>")
    return (f"<div class='doc'><div class='who'>{who}</div><div class='amt'>{e(amount)}</div>"
            f"<div class='sub'>{e(sub)}</div><div class='acts'>{' '.join(acts)}</div></div>")


def _fetch():
    from vula.storage_links import fetch_sync
    return fetch_sync


@router.get("/track/{token}/doc/{doc_id}", response_class=HTMLResponse)
async def project_doc_view(token: str, doc_id: str, request: Request):
    """One document, view only: its pages as watermarked pictures, no file to save."""
    import html as _html
    from vula.commerce import project_share_docs as sd
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, _view, _lines = got
    d = await asyncio.to_thread(sd.find, share["tenant_id"], share["project"], doc_id, brand.get("name") or "")
    if not d or not sd.viewable(d):
        return HTMLResponse(_GONE, status_code=404)
    try:
        n = await asyncio.to_thread(sd.page_count, d, _fetch())
    except Exception as exc:  # noqa: BLE001
        log.warning("project link document pages failed: %s", exc)
        n = 0
    e = _html.escape
    base = f"/track/{e(token)}"
    acc = await asyncio.to_thread(sd.access, share)
    if acc["all"] or str(d["id"]) in acc["docs"]:
        action = f"<a class='btn' href='{base}/download/{e(str(d['id']))}'>Download</a>"
    elif str(d["id"]) in acc["asked"] or "all" in acc["asked"]:
        action = "<span>Download requested — waiting for the team.</span>"
    else:
        action = (f"<form method='post' action='{base}/request' style='display:inline'>"
                  f"<input type='hidden' name='scope' value='{e(str(d['id']))}'>"
                  "<button class='btn' type='submit'>Request download</button></form>")
    pages = "".join(f"<img src='{base}/doc/{e(str(d['id']))}/{i}.png' alt='Page {i}' loading='lazy'>"
                    for i in range(1, n + 1)) or "<p>This document can't be shown just now.</p>"
    accent = e(brand.get("accent_color") or "#2C5545")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{e(sd.label(d))}</title><style>
body{{margin:0;background:#e9e6df;font-family:system-ui,-apple-system,sans-serif;color:#2a2a2a}}
*{{box-sizing:border-box}}
.bar{{position:sticky;top:0;background:#fff;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;
box-shadow:0 1px 6px rgba(0,0,0,.08);z-index:1}}.bar b{{flex:1 1 200px;min-width:0;overflow-wrap:anywhere;font-size:15px}}
.btn{{padding:7px 12px;border-radius:8px;border:1px solid {accent};background:#fff;color:{accent};font:600 13px system-ui;
text-decoration:none;cursor:pointer}}
img{{display:block;width:min(900px,calc(100% - 16px));height:auto;margin:14px auto;box-shadow:0 2px 10px rgba(0,0,0,.12);background:#fff;
user-select:none;-webkit-user-drag:none;pointer-events:none}}</style></head>
<body oncontextmenu="return false"><div class="bar"><a class="btn" href="{base}">← Back</a><b>{e(sd.label(d))}</b>{action}</div>
{pages}</body></html>"""
    return HTMLResponse(page, headers=_NO_STORE)


@router.get("/track/{token}/doc/{doc_id}/{page}.png")
async def project_doc_page(token: str, doc_id: str, page: int, request: Request):
    from vula.commerce import project_share_docs as sd
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, _view, _lines = got
    d = await asyncio.to_thread(sd.find, share["tenant_id"], share["project"], doc_id, brand.get("name") or "")
    if not d or not sd.viewable(d):
        return HTMLResponse(_GONE, status_code=404)
    mark = f"View only · {brand.get('name') or ''} · {share['project']}"
    try:
        png = await asyncio.to_thread(sd.page_png, d, page, _fetch(), mark)
    except IndexError:
        return HTMLResponse(_GONE, status_code=404)
    return Response(content=png, media_type="image/png",
                    headers={**_NO_STORE, "Content-Disposition": "inline"})


@router.post("/track/{token}/request")
async def project_doc_request(token: str, request: Request):
    """The client asks to download one document or all of them; the owner and managers are asked."""
    from fastapi.responses import RedirectResponse
    from vula.commerce import project_share_docs as sd
    from vula.commerce import project_tracker as pt
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, _view, _lines = got
    form = await request.form()
    scope = str(form.get("scope") or "all")[:64]
    try:
        res = await asyncio.to_thread(sd.ask, share, scope, brand.get("name") or "")
    except ValueError:
        return HTMLResponse(_GONE, status_code=404)
    if res["new"]:
        try:
            what = "all the documents" if scope == "all" else await asyncio.to_thread(
                lambda: sd.label(sd.find(share["tenant_id"], share["project"], scope, brand.get("name") or "") or {}))
            await _ask_owner(share, res["request"]["id"], sd.prompt(share["project"],
                                                                     pt.VIEW_LABEL.get(share.get("view"), ""), what))
        except Exception as exc:  # noqa: BLE001 — the request is saved; the dashboard shows it
            log.warning("download request notice failed: %s", exc)
    return RedirectResponse(f"/track/{token}?asked=1", status_code=303)


async def _ask_owner(share: dict, request_id: str, text: str) -> None:
    from vula import open_questions as oq
    from vula.api.whatsapp import _send_reply, _slip_approvers
    for to in _slip_approvers(share["tenant_id"]):
        oq.ask(share["tenant_id"], to, "doc_download", request_id, text)
        await _send_reply(to, text, tenant_id=share["tenant_id"])


@router.get("/track/{token}/download/{doc_id}")
async def project_doc_download(token: str, doc_id: str, request: Request):
    """The original file — only while the business's yes for it (or for all) is current."""
    from vula.commerce import project_share_docs as sd
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, _view, _lines = got
    d = await asyncio.to_thread(sd.find, share["tenant_id"], share["project"], doc_id, brand.get("name") or "")
    if not d or not d.get("file_url"):
        return HTMLResponse(_GONE, status_code=404)
    if not await asyncio.to_thread(sd.may_download, share, str(d["id"])):
        return HTMLResponse(_NOT_YET, status_code=403)
    data = await asyncio.to_thread(_fetch(), d["file_url"])
    name = "".join(ch for ch in sd.label(d) if ch.isalnum() or ch in " -_.").strip()[:80] or "document"
    return Response(content=data, media_type="application/octet-stream",
                    headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{name}{d.get("ext") or ""}"'})


@router.get("/track/{token}/download.zip")
async def project_docs_zip(token: str, request: Request):
    """Every client document as the project file's client copy — only after a yes for 'all'."""
    from vula.commerce import project_file, project_share_docs as sd
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, _view, _lines = got
    if not await asyncio.to_thread(sd.may_download, share):
        return HTMLResponse(_NOT_YET, status_code=403)
    try:
        data, man = await asyncio.to_thread(project_file.build_pack, share["tenant_id"], share["project"],
                                            "client", brand.get("name") or "")
    except (LookupError, RuntimeError) as exc:
        log.warning("client link zip failed: %s", exc)
        return HTMLResponse(_GONE, status_code=404)
    name = project_file.zip_filename(man["project"], "client", man["as_of"])
    return Response(content=data, media_type="application/zip",
                    headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/track/{token}/pdf")
async def project_page_pdf(token: str, request: Request):
    from vula.commerce import project_tracker as pt
    got = await _open(token, request)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    _share, brand, view, _lines = got
    return _pdf(await asyncio.to_thread(pt.pdf, brand, view), f"{_name(view)}_account")


@router.get("/track/{token}/bill.pdf")
async def project_bill_pdf(token: str, request: Request):
    from vula.commerce import boq_online
    got = await _open(token, request)
    if not got or not got[3]:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, view, _lines = got
    data = await asyncio.to_thread(boq_online.pdf, share["tenant_id"], share["project"], brand)
    return _pdf(data, f"{_name(view)}_priced_bill")
