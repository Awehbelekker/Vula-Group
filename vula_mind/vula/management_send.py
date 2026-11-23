"""vula/management_send.py — last month's management accounts to the owners, around the 5th
(23 Nov 2026; Ian: owners only — managers and the accountants download it from the dashboard).

For each business with the `management_accounts` switch on (vula/features.py; off by default), from
08:00 SAST on the 5th — or on any later day of the month if a send was missed — every active owner gets
last month's pack (vula/commerce/management_pack.py) once:

  1. WhatsApp: the PDF as a document with a short summary (inside the 24-hour window);
  2. else the approved `vula_document_share` template with a week-long link to the PDF;
  3. else email (the business's own mailbox, else Vula's) with the PDF and the Excel attached.

Never a month that ended before the business's send was switched on: the first time the switch is
seen on, that day is kept (`features.management_accounts_since`), so switching it on mid-month starts with
the month then running. A month with no money in or out isn't sent. Each send is kept in vula_merchant_audit
(`management_pack_sent`, with the month and who it went to) and a month already sent to an owner is
never sent again — the scheduler can run on two workers at once, so the record is read before sending.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
SEND_DAY = 5
SEND_HOUR = 8
ACTION = "management_pack_sent"
SWITCH = "management_accounts"


def _client():
    from vula.commerce import service
    return service._client()


def due_month(now: Optional[datetime] = None) -> Optional[str]:
    """Last month (YYYY-MM) once it's the 5th from 08:00 SAST or later; None before then."""
    now = (now or datetime.now(SAST)).astimezone(SAST)
    if now.day < SEND_DAY or (now.day == SEND_DAY and now.hour < SEND_HOUR):
        return None
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def _sent_to(tenant_id: str, month: str) -> set:
    """Who already got this month's pack (phone or email), from the audit record."""
    try:
        rows = (_client().table("vula_merchant_audit").select("detail").eq("tenant_id", tenant_id)
                .eq("action", ACTION).order("created_at", desc=True).limit(500).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — unreadable: send nothing rather than twice
        raise RuntimeError(f"couldn't read what was already sent: {exc}") from exc
    return {str((r.get("detail") or {}).get("to")) for r in rows if (r.get("detail") or {}).get("month") == month}


def owners(tenant_id: str) -> List[Dict[str, Any]]:
    from vula import team_index
    return [m for m in team_index.active_members(tenant_id)
            if (m.get("role") or "") == "owner" and (m.get("whatsapp") or m.get("email"))]


def _has_money(f: Dict[str, Any]) -> bool:
    t = f["pnl"]["totals"]["this"]
    return bool(t["income_cents"] or t["expense_cents"] or f["cash"]["in_cents"] or f["cash"]["out_cents"])


def _link(tenant_id: str, month: str, pdf: bytes) -> Optional[str]:
    try:
        from vula.api.whatsapp import _upload_to_storage
        from vula.storage_links import SHARE_TTL, signed
        url = _upload_to_storage("documents", f"{tenant_id}/reports/management_{month}.pdf", pdf, "application/pdf")
        return signed(url, SHARE_TTL) if url else None
    except Exception as exc:  # noqa: BLE001
        log.warning("management pack: link not made for %s: %s", tenant_id, exc)
        return None


async def _whatsapp(tenant_id: str, phone: str, f: Dict[str, Any], pdf: bytes, caption: str,
                    link: Optional[str]) -> Optional[str]:
    from vula.api import whatsapp
    from vula.commerce import doc_send, management_pack as mp
    name = mp.filename(f, "pdf")
    if await whatsapp._send_invoice_document(phone, pdf, name, caption=caption, tenant_id=tenant_id,
                                             content_type="application/pdf"):
        return "WhatsApp"
    if link and await whatsapp.send_template_message(
            tenant_id, phone, doc_send.TEMPLATE,
            [f["business"], f"the management accounts for {f['period']['this']['label']}"],
            header_document={"link": link, "filename": name}):
        return "WhatsApp (message template)"
    return None


async def _email(tenant_id: str, to: str, f: Dict[str, Any], pdf: bytes, xlsx: bytes, caption: str) -> Optional[str]:
    from vula.commerce import mail_router, management_pack as mp
    subject = f"Management accounts, {f['period']['this']['label']} – {f['business']}"
    body = caption.replace("*", "") + "\n\nThe pack is attached as a PDF and as Excel.\n\nVula"
    files = [{"filename": mp.filename(f, "pdf"), "content": pdf, "mimetype": "application/pdf"},
             {"filename": mp.filename(f, "xlsx"), "content": xlsx,
              "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]
    if await mail_router.send_tenant_email(tenant_id, to, subject, body, attachments=files):
        return "email (your mailbox)"
    from vula.api import email as vula_email
    html = "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    if await vula_email._send(to, subject, html, body,
                              attachments=[{"filename": x["filename"], "content": base64.b64encode(x["content"]).decode()}
                                           for x in files]):
        return "email (Vula's)"
    return None


async def send_month(tenant_id: str, month: Optional[str] = None, *, actor: Optional[dict] = None,
                     only: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Send `month`'s pack (default: last month) to every owner who hasn't had it."""
    from vula.api.merchant_audit import audit
    from vula.commerce import management_pack as mp
    if not month:
        now = datetime.now(SAST)
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        month = f"{y:04d}-{m:02d}"
    f = await asyncio.to_thread(mp.figures, tenant_id, month)
    month = f["period"]["month"]
    if not _has_money(f):
        return {"month": month, "sent": [], "skipped": "no money in or out that month"}
    people = only if only is not None else owners(tenant_id)
    if not people:
        return {"month": month, "sent": [], "skipped": "no active owner with a WhatsApp number or email"}
    done = _sent_to(tenant_id, month)
    todo = [m for m in people if str(m.get("whatsapp") or m.get("email")) not in done]
    if not todo:
        return {"month": month, "sent": [], "skipped": "already sent"}
    pdf = await asyncio.to_thread(mp.pdf, f)
    xlsx = await asyncio.to_thread(mp.workbook, f)      # read back before anything goes out
    caption = mp.summary_text(f)
    link = await asyncio.to_thread(_link, tenant_id, month, pdf) if any(m.get("whatsapp") for m in todo) else None
    sent, failed = [], []
    for m in todo:
        to = str(m.get("whatsapp") or m.get("email"))
        via = None
        if m.get("whatsapp"):
            via = await _whatsapp(tenant_id, m["whatsapp"], f, pdf, caption, link)
        if not via and m.get("email"):
            via = await _email(tenant_id, m["email"], f, pdf, xlsx, caption)
        if via:
            audit(tenant_id, actor or {"email": "vula (monthly send)"}, ACTION, month=month, to=to,
                  name=m.get("name") or "", via=via)
            sent.append({"name": m.get("name") or "", "via": via})
        else:
            failed.append(m.get("name") or "an owner")
    if failed:
        log.warning("management pack for %s %s not sent to %d owner(s)", tenant_id, month, len(failed))
    return {"month": month, "sent": sent, "failed": failed}


def _tenants() -> List[str]:
    from vula import features
    from vula.api import tenants as _t
    try:
        rows = _client().table("vula_tenant_config").select("tenant_id").execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("management pack: tenant list failed: %s", exc)
        return []
    return [r["tenant_id"] for r in rows if r.get("tenant_id") and _t.is_active(r["tenant_id"])
            and features.on(r["tenant_id"], SWITCH)]


SINCE = "management_accounts_since"


def _since(tenant_id: str, today: date) -> Optional[date]:
    """The day the business's send was switched on. The first time the switch is seen on, today is
    recorded (vula_tenant_config.features), so switching it on mid-month never sends an older month
    (23 Nov 2026: switched on for DIGG on the 23rd, it would have sent October at once)."""
    db = _client()
    rows = db.table("vula_tenant_config").select("features").eq("tenant_id", tenant_id).limit(1).execute().data or []
    if not rows:
        return None
    feats = dict(rows[0].get("features") or {})
    try:
        return date.fromisoformat(str(feats[SINCE])[:10])
    except (KeyError, TypeError, ValueError):
        pass
    feats[SINCE] = today.isoformat()
    db.table("vula_tenant_config").update({"features": feats}).eq("tenant_id", tenant_id).execute()
    log.info("management pack: %s switched on, first month to send is the one ending after %s", tenant_id, today)
    return today


def _month_end(month: str) -> date:
    y, m = (int(x) for x in month.split("-"))
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


async def tick(now: Optional[datetime] = None) -> int:
    """One pass: every business with the switch on gets last month's pack, once it's due — never a
    month that ended before its send was switched on. (Master's send-now route sends any month.)"""
    now = (now or datetime.now(SAST)).astimezone(SAST)
    month = due_month(now)
    if not month:
        return 0
    sent = 0
    for tid in _tenants():
        try:
            since = _since(tid, now.date())
            if since is None or _month_end(month) < since:
                continue
            out = await send_month(tid, month)
            sent += len(out.get("sent") or [])
            if out.get("sent"):
                log.info("management pack %s for %s: sent to %d owner(s)", month, tid, len(out["sent"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("management pack %s for %s failed: %s", month, tid, exc)
    return sent


async def run_forever() -> None:
    await asyncio.sleep(900)    # settle on boot
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("management pack tick failed: %s", exc)
        await asyncio.sleep(3600)
