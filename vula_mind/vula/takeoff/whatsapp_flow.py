"""
vula/takeoff/whatsapp_flow.py — a plan sent on WhatsApp: read, preview, confirm, then priced.

2026-11-01 (owner: "WhatsApp first, as for the rest of Vula"; "the measured rooms are confirmed
before anything is priced — preview → correct → confirm"). The owner or a staff member of a
business with Estimating switched on sends a drawing (DXF, or a PDF that is a drawing). Vula:

1. reads it with the same pipeline as the dashboard (vula/takeoff/api.py: create_job →
   _process_plans), which always stops at needs_review;
2. sends the measured rooms and anything to check, with a link to correct them in the dashboard;
3. asks Price it / Not now (_ask_admin_confirm → commerce_admin "takeoff_approve", dispatch only).
   Nothing is priced until Price it. The reply then gives the totals, and the dashboard link to
   make it the project's BOQ.

Any other file, sender or business falls through to normal document filing (returns False).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CAPTION = re.compile(r"\b(take ?off|measure|plans?|drawings?|floor ?plan|boq|quantities)\b", re.IGNORECASE)
_SHEET = re.compile(r"\b[AS]\d{3,4}\b")
_SCALE = re.compile(r"\b1\s?:\s?(20|25|50|75|100|125|200|250|500)\b")


def _estimating_on(tenant_id: str) -> bool:
    try:
        from vula.api.tenants import enabled_modules
        return "estimating" in (enabled_modules(tenant_id) or [])
    except Exception:  # noqa: BLE001
        return False


def looks_like_a_drawing(path: Path) -> bool:
    """A PDF whose first page is larger than A4 and carries a sheet number or a drawing scale —
    a drawing, not an invoice or statement. Never guesses on anything else."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return False
            page = pdf.pages[0]
            big = max(page.width, page.height) > 900          # A4 is 842 pt on its long side
            text = (page.extract_text() or "")[:20000]
        return big and bool(_SHEET.search(text) or _SCALE.search(text))
    except Exception:  # noqa: BLE001
        return False


def is_plan(path: Path, caption: str = "") -> bool:
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        return True
    if suffix == ".pdf":
        return bool(_CAPTION.search(caption or "")) or looks_like_a_drawing(path)
    return False


def preview_text(job: dict, link: str) -> str:
    """What the owner reads before Price it: the rooms as measured, what to check, how to correct."""
    p = job.get("project") or {}
    rooms = p.get("room_list") or []
    lines = [f"📏 *{p.get('title') or job.get('filename') or 'Your plans'}* — measured from the drawing:"]
    if rooms:
        for r in rooms[:25]:
            finish = f" · {r['floor'].replace('_', ' ')}" if r.get("floor") else ""
            lines.append(f"• {r['name']}: {r['area']:.1f} m²{finish}")
        if len(rooms) > 25:
            lines.append(f"…and {len(rooms) - 25} more rooms")
        lines.append(f"Total {sum(r['area'] for r in rooms):.1f} m²"
                     + (f" (floor area {p['gfa']:.0f} m²)" if p.get("gfa") else ""))
    else:
        lines.append("No rooms could be read from this drawing.")
    reasons = (job.get("review") or {}).get("reasons") or []
    if reasons:
        lines.append("\nPlease check:")
        lines.extend(f"• {r}" for r in reasons[:5])
    lines.append(f"\nTo correct a room, a finish or the wall height, open: {link}")
    lines.append("Nothing is priced until you tap *Price it*.")
    return "\n".join(lines)


async def plan_first(tenant_id: str, phone: str, local_path: Path, filename: str, caption: str = "",
                     is_staff: bool = True) -> bool:
    """True when the file was a plan and has been handled (read and sent for confirmation)."""
    if not is_staff or not _estimating_on(tenant_id) or not is_plan(Path(local_path), caption):
        return False
    from vula.api import whatsapp as wa
    from vula.takeoff import api
    await wa._send_reply(phone, "📏 Reading your plans — I'll send the measured rooms to check in a "
                                "few minutes. Nothing is priced until you confirm.", tenant_id)
    try:
        job_id, path = api.create_job(tenant_id, filename, Path(local_path).read_bytes(), source="whatsapp")
        await api._process_plans(job_id, path, tenant_id, 15.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("WhatsApp takeoff failed for %s: %s", tenant_id, exc)
        await wa._send_reply(phone, "Sorry, I couldn't read those plans. Try uploading them in the "
                                    "dashboard's Takeoff instead.", tenant_id)
        return True
    job = api._jobs.get(job_id) or {}
    if job.get("status") != "needs_review":
        await wa._send_reply(phone, f"Sorry, I couldn't read those plans ({job.get('error') or 'no rooms'}). "
                                    "Try uploading them in the dashboard's Takeoff instead.", tenant_id)
        return True
    link = wa._dashboard_link(tenant_id, "takeoff")
    text = preview_text(job, link)
    if not (job.get("project") or {}).get("room_list"):
        await wa._send_reply(phone, text.replace("Nothing is priced until you tap *Price it*.",
                                                 "Add the rooms there and confirm."), tenant_id)
        return True
    await wa._ask_admin_confirm(phone, tenant_id, "takeoff_approve", {"job_id": job_id}, {},
                                caller_role="owner", summary=text, labels=("Price it", "Not now"))
    return True


def priced_text(job: dict, link: str) -> str:
    boq = job.get("boq") or {}
    cents = int(boq.get("grand_total_cents") or 0)
    lines = [f"✅ Priced: {len(boq.get('items') or [])} lines, R{cents / 100:,.2f} excl. VAT "
             f"(incl. {boq.get('markup_pct', 15):.0f}% markup)."]
    no_rate = sum(1 for i in boq.get("items") or [] if not i.get("rate_cents"))
    if no_rate:
        lines.append(f"{no_rate} line(s) have no rate yet — add them in the BOQ.")
    if boq.get("excluded"):
        lines.append("Not included: " + "; ".join(e.split(" — ")[0] for e in boq["excluded"][:4]) + ".")
    lines.append(f"See it, download it, or make it a project's BOQ: {link}")
    return "\n".join(lines)


async def approve(tenant_id: str, job_id: str, by: Optional[str]) -> str:
    """The owner tapped Price it: approve the quantities as measured and price them."""
    from vula.takeoff import api
    job = api._job_for(job_id, tenant_id)
    if not job:
        return "I couldn't find that takeoff any more — it may have been deleted."
    try:
        status, project, _ = api.decide(job_id, job, api.ReviewDecision(decision="approve",
                                                                          note="Price it on WhatsApp"),
                                        by or "whatsapp")
    except api.ReviewError as exc:
        return str(exc)
    await api.price_approved(job_id, job, project)
    from vula.api import whatsapp as wa
    link = wa._dashboard_link(tenant_id, "takeoff")
    if job.get("status") != "complete":
        return f"The quantities are confirmed, but pricing failed ({job.get('error') or 'unknown'}). Open {link} to try again."
    return priced_text(job, link)
