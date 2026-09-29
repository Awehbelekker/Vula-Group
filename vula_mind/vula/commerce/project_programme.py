"""Run a project from its programme, against one signed cost baseline.

2026-09-29, Judy (DIGG, Atlantis Foods Paarden Island office): "Input data is ClickUp for the
project programme, or any Gantt … a four-week programme, very tight, and connected to cost, so
need to track that daily. We also need to task the staff … WhatsApps, what is the tasks due for
the day per room … we signed cost revision 10, and that was the invoice made out to the client.
All project costs need to be managed against that. Input data only, that one document; any
variation to it needs to be flagged with a variation order."

- set_baseline(): the signed estimate becomes the project's locked BOQ (vula_project_boq,
  migration 188): one section per line (Rev10 is priced per room) plus whatever of the total the
  lines don't carry (contingency, profit). A later estimate never replaces it.
- read_programme() / import_programme(): a programme or Gantt (PDF or spreadsheet) becomes
  field-ops tasks with a room, trade, start and end date and who does it — so the existing
  DONE / photo-evidence / sign-off flow on WhatsApp works for them unchanged.
- morning_briefs(): each morning every site person gets today's tasks per room, and the owner
  gets the day's work, what's overdue, spend against the baseline and anything that needs a
  variation order.

All money is integer cents. Nothing here guesses a figure: costs come from job costing (the
bank, allocated to the project), budgets from the signed document's own lines.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
REMAINDER = "Contingency, profit & other (in the signed total)"
_PG_RE = re.compile(r"(\d+)\s*weeks?.{0,20}?R\s?([\d\s,]+)\s*/\s*(?:week|wk)", re.IGNORECASE)
_DONE = ("complete", "rejected")


def _client():
    from vula.commerce import service
    return service._client()


def _canon(tenant_id: str, project: str) -> str:
    from vula.commerce.service import canonical_project
    return canonical_project(tenant_id, project) or project


def _r(cents: int) -> str:
    return f"R{int(cents or 0) / 100:,.0f}"


def today_sast() -> date:
    return datetime.now(SAST).date()


# ── The signed baseline ──────────────────────────────────────────────────────

def find_document(tenant_id: str, ref: str) -> Optional[Dict[str, Any]]:
    """A filed document by its id, KB doc id, or (part of) its filename — newest first."""
    ref = (ref or "").strip()
    if not ref:
        return None
    db = _client()
    for col in ("id", "doc_id"):
        try:
            rows = (db.table("vula_filed_documents").select("*").eq("tenant_id", tenant_id)
                    .eq(col, ref).limit(1).execute().data or [])
            if rows:
                return rows[0]
        except Exception:
            pass
    rows = (db.table("vula_filed_documents").select("*").eq("tenant_id", tenant_id)
            .ilike("filename", f"%{ref}%").order("created_at", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None


def room_of(description: str) -> str:
    """ "Small Meeting Room A (10m²)" → "Small Meeting Room A"."""
    return re.sub(r"\s*\(.*?\)\s*", " ", description or "").split("—")[0].strip(" -–") or (description or "")


def baseline_sections(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The signed document's lines as budget sections, excl. VAT, plus the part of the total the
    lines don't carry, so the sections always add up to the signed total."""
    lines = [li for li in (fields.get("line_items") or []) if isinstance(li, dict) and li.get("description")]
    total = int(fields.get("total_cents") or 0)
    vat = int(fields.get("vat_cents") or 0)
    sections = []
    for li in lines:
        cents = int(li.get("total_cents") or 0)
        sec = {"section": room_of(li["description"]), "description": li["description"],
               "budget_cents": cents}
        m = _PG_RE.search(li["description"])
        if m:
            sec["pg_weeks"] = int(m.group(1))
            sec["pg_week_cents"] = int(re.sub(r"\D", "", m.group(2))) * 100
        sections.append(sec)
    lines_sum = sum(s["budget_cents"] for s in sections)
    excl = (total - vat) if total else lines_sum
    if excl > lines_sum:
        sections.append({"section": REMAINDER, "budget_cents": excl - lines_sum})
    return {"sections": sections, "total_excl_cents": max(excl, lines_sum),
            "total_incl_cents": total or max(excl, lines_sum), "vat_cents": vat}


def set_baseline(tenant_id: str, project: str, doc_ref: str) -> Dict[str, Any]:
    """Make a signed estimate the project's only cost baseline. Replaces an earlier baseline only
    because the owner explicitly asked (this call); nothing else ever does."""
    from vula.commerce.service import upsert_project_boq
    doc = find_document(tenant_id, doc_ref)
    if not doc:
        raise ValueError(f"No filed document matches '{doc_ref}'.")
    project = _canon(tenant_id, project)
    b = baseline_sections(doc.get("fields") or {})
    if not b["sections"]:
        raise ValueError(f"'{doc.get('filename')}' has no priced lines to use as a baseline.")
    upsert_project_boq(tenant_id, project, b["total_excl_cents"], title=doc.get("filename"),
                       sections=b["sections"], force=True)
    _client().table("vula_project_boq").update({
        "baseline_doc_id": doc["id"], "baseline_locked": True,
        "baseline_set_at": datetime.now(timezone.utc).isoformat(),
    }).eq("tenant_id", tenant_id).eq("project", project).execute()
    got = baseline(tenant_id, project)            # read back — reported only if it's really there
    if not got or not got.get("baseline_locked") or got.get("baseline_doc_id") != doc["id"]:
        raise RuntimeError("The baseline didn't save — nothing changed.")
    return {"project": project, "document": doc.get("filename"), **b}


def baseline(tenant_id: str, project: str) -> Optional[Dict[str, Any]]:
    try:
        rows = (_client().table("vula_project_boq").select("*").eq("tenant_id", tenant_id)
                .eq("project", _canon(tenant_id, project)).limit(1).execute().data or [])
    except Exception as exc:
        log.debug("baseline read skipped: %s", exc)
        return None
    return rows[0] if rows else None


# ── The programme ────────────────────────────────────────────────────────────

def _iso(value: Any, year_hint: int) -> Optional[str]:
    """A programme date as YYYY-MM-DD. A model reading "Sat 19 Sep" sometimes invents the year
    (DIGG's weekend programme came back as 2020): anything more than a year off the hint is
    moved to the hint's year."""
    s = str(value or "").strip()
    if not s:
        return None
    d = None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    try:
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(s, fmt).date()
                    break
                except ValueError:
                    continue
    except ValueError:
        return None
    if d is None:
        return None
    if abs(d.year - year_hint) >= 1:
        try:
            d = d.replace(year=year_hint)
        except ValueError:
            return None
    return d.isoformat()


def normalise_tasks(raw: List[Dict[str, Any]], year_hint: int) -> List[Dict[str, Any]]:
    out = []
    for t in raw or []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("task") or t.get("title") or "").strip()
        if not title:
            continue
        start = _iso(t.get("start"), year_hint)
        end = _iso(t.get("end") or t.get("due"), year_hint) or start
        start = start or end
        if start and end and end < start:
            start, end = end, start
        out.append({"title": title[:300], "room": (str(t.get("room") or "").strip() or "General")[:80],
                    "trade": str(t.get("trade") or "").strip()[:60],
                    "assignee": str(t.get("assignee") or "").strip()[:80],
                    "start": start, "end": end, "done": bool(t.get("done")),
                    "notes": str(t.get("notes") or "")[:300]})
    return out


async def _document_text(tenant_id: str, doc: Dict[str, Any]) -> str:
    from vula.commerce.reread import _download, _file_text
    if not doc.get("file_url"):
        return ""
    data = await _download(doc["file_url"])
    name = Path((doc.get("filename") or "programme.pdf").replace("\r", " ").replace("\n", " ")).name
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        if name.lower().endswith((".xlsx", ".xlsm")):
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            rows = []
            for ws in wb.worksheets:
                for r in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in r if c not in (None, "")]
                    if cells:
                        rows.append(" | ".join(cells))
            return "\n".join(rows)
        if name.lower().endswith(".csv"):
            return data.decode("utf-8", "replace")
        return await _file_text(tenant_id, doc, path)


_PROGRAMME_PROMPT = (
    "This is part of a construction programme / Gantt chart. Return STRICT JSON only: "
    '{"tasks": [{"task": string, "room": string|null, "trade": string|null, '
    '"assignee": string|null, "start": "YYYY-MM-DD"|null, "end": "YYYY-MM-DD"|null}]} — one entry '
    "per task bar or row. room = the room/area the task is in (e.g. Reception, Main Boardroom, "
    "Open Plan); assignee = the person or subcontractor named for it. Dates exactly as the "
    "programme shows them, in the year {year} unless the programme states another. Skip summary "
    "rows, headings and milestones with no work. Never invent a task, room, person or date."
)


async def read_programme(tenant_id: str, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The programme's tasks, read from the filed document (PDF, spreadsheet or CSV)."""
    import litellm
    from core.llm_router import resolve_cheap_route
    text = await _document_text(tenant_id, doc)
    if not text.strip():
        return []
    year = today_sast().year
    model, api_key, api_base = await resolve_cheap_route()
    raw: List[Dict[str, Any]] = []
    for i in range(0, len(text), 7000):
        chunk = text[i:i + 7000]
        try:
            resp = await litellm.acompletion(
                model=model, api_key=api_key, api_base=api_base, temperature=0, max_tokens=3000,
                messages=[{"role": "system", "content": _PROGRAMME_PROMPT.replace("{year}", str(year))},
                          {"role": "user", "content": f"Filename: {doc.get('filename')}\n\n{chunk}\n\nJSON:"}])
            body = (resp.choices[0].message.content or "").replace("```json", "").replace("```", "")
            a, b = body.find("{"), body.rfind("}")
            data = json.loads(body[a:b + 1]) if a >= 0 and b > a else {}
            raw.extend(t for t in data.get("tasks") or [] if isinstance(t, dict)
                       and str(t.get("task") or "").strip()
                       and str(t.get("task")).strip()[:25].lower() in chunk.lower())   # grounded
        except Exception as exc:
            log.debug("programme chunk read failed for %s: %s", doc.get("filename"), exc)
    return normalise_tasks(raw, year)


def _people(tenant_id: str) -> List[Dict[str, Any]]:
    """Everyone who can be sent tasks: field-ops site staff first, then the team."""
    out = []
    try:
        from vula.models.field_ops import FieldOpsDB
        out += [{"id": c.id, "name": c.name, "phone": c.phone, "kind": "site"}
                for c in FieldOpsDB().list_contractors(tenant_id)]
    except Exception as exc:
        log.debug("site staff read skipped: %s", exc)
    try:
        from vula import team_index
        out += [{"id": None, "name": m.get("name") or "", "phone": m.get("whatsapp") or "",
                 "kind": "team"} for m in team_index.active_members(tenant_id)]
    except Exception as exc:
        log.debug("team read skipped: %s", exc)
    return out


def match_person(people: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """The person a programme names — full name, or a unique first name ("Sipho")."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not n:
        return None
    exact = [p for p in people if p["name"].strip().lower() == n]
    if exact:
        return exact[0]
    first = [p for p in people if p["name"].strip().lower().split(" ")[:1] == n.split(" ")[:1]]
    return first[0] if len(first) == 1 else None


_NAME_SPLIT = re.compile(r"\s*(?:/|,|&|\+|\band\b)\s*", re.IGNORECASE)


def split_names(assignee: str) -> List[str]:
    return [n.strip() for n in _NAME_SPLIT.split(assignee or "") if n.strip()]


def import_programme(tenant_id: str, project: str, doc: Dict[str, Any],
                     tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace the project's open programme tasks with these (tasks already done are kept)."""
    project = _canon(tenant_id, project)
    db = _client()
    db.table("vula_field_tasks").delete().eq("tenant_id", tenant_id).eq("project_id", project) \
        .eq("source", "programme").not_.in_("status", list(_DONE)).execute()
    people = _people(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    rows, unmatched = [], set()
    for t in tasks:
        # "Electrical & Plumbing Second Fix (Elyas / Edison)": one row per person, so each gets it
        for name in split_names(t["assignee"]) or [""]:
            who = match_person(people, name) if name else None
            if name and not (who and who["kind"] == "site"):
                unmatched.add(name)
            rows.append({"id": uuid.uuid4().hex, "tenant_id": tenant_id, "project_id": project,
                         "title": t["title"], "trade": t["trade"],
                         "status": "complete" if t.get("done") else "pending",
                         "assigned_to": (who or {}).get("id") or "", "due_date": t["end"] or "",
                         "start_date": t["start"] or "", "room": t["room"],
                         "assignee_name": name, "source": "programme",
                         "source_doc_id": doc.get("id"), "notes": t.get("notes") or "",
                         "created_at": now, "updated_at": now})
    for i in range(0, len(rows), 200):
        db.table("vula_field_tasks").insert(rows[i:i + 200]).execute()
    saved = (db.table("vula_field_tasks").select("id").eq("tenant_id", tenant_id)
             .eq("project_id", project).eq("source", "programme").eq("source_doc_id", doc.get("id"))
             .execute().data or [])
    dates = sorted(d for t in tasks for d in (t["start"], t["end"]) if d)
    return {"project": project, "document": doc.get("filename"), "tasks": len(saved),
            "rooms": sorted({t["room"] for t in tasks}),
            "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
            "needs_number": sorted(unmatched)}


def add_site_staff(tenant_id: str, project: str, name: str, phone: str, trade: str = "") -> Dict[str, Any]:
    """Add (or update) a site person and give them the programme tasks that name them."""
    from vula.models.field_ops import FieldOpsDB
    project = _canon(tenant_id, project)
    fdb = FieldOpsDB()
    c = fdb.upsert_contractor(tenant_id, name.strip(), phone, trade.strip())
    fdb.assign_to_project(tenant_id, project, c.id)
    rows = (_client().table("vula_field_tasks").select("id,assignee_name").eq("tenant_id", tenant_id)
            .eq("project_id", project).eq("source", "programme").execute().data or [])
    mine = [r["id"] for r in rows
            if match_person([{"id": c.id, "name": c.name, "phone": c.phone, "kind": "site"}],
                            r.get("assignee_name") or "")]
    if mine:
        _client().table("vula_field_tasks").update({"assigned_to": c.id}).in_("id", mine).execute()
    return {"id": c.id, "name": c.name, "phone": c.phone, "tasks": len(mine)}


def programme_tasks(tenant_id: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    q = (_client().table("vula_field_tasks").select("*").eq("tenant_id", tenant_id)
         .eq("source", "programme"))
    if project:
        q = q.eq("project_id", _canon(tenant_id, project))
    try:
        return q.execute().data or []
    except Exception as exc:
        log.debug("programme tasks read skipped (run migration 188?): %s", exc)
        return []


def day_plan(tasks: List[Dict[str, Any]], day: date) -> Dict[str, List[Dict[str, Any]]]:
    """Today's work (a task whose start..end spans the day) and what's overdue."""
    d = day.isoformat()
    today, overdue = [], []
    for t in tasks:
        if t.get("status") in _DONE:
            continue
        start, end = t.get("start_date") or t.get("due_date") or "", t.get("due_date") or ""
        if start and end and start <= d <= end:
            today.append(t)
        elif end and end < d:
            overdue.append(t)
    key = lambda t: ((t.get("room") or "General").lower(), t.get("trade") or "", t.get("title") or "")
    return {"today": sorted(today, key=key), "overdue": sorted(overdue, key=key)}


def _by_room(tasks: List[Dict[str, Any]], with_who: bool = False) -> str:
    lines, room = [], None
    for t in tasks:
        if (t.get("room") or "General") != room:
            room = t.get("room") or "General"
            lines.append(f"\n*{room}*")
        who = f" — {t.get('assignee_name')}" if with_who and t.get("assignee_name") else ""
        lines.append(f"• {t['title']}{who}")
    return "\n".join(lines).strip()


# ── Cost against the baseline ────────────────────────────────────────────────

def cost_position(tenant_id: str, project: str, day: date,
                  tasks: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    b = baseline(tenant_id, project)
    if not b or not b.get("baseline_locked"):
        return None
    from vula.commerce import job_costing
    from vula.commerce.service import project_key
    project = b["project"]
    res = job_costing.costing(tenant_id)
    mine = next((p for p in res.get("projects") or [] if project_key(p["project"]) == project_key(project)), {})
    budget = int(b.get("total_cents") or 0)
    spent = int(mine.get("cost_cents") or 0)
    out: Dict[str, Any] = {"project": project, "budget_cents": budget, "spent_cents": spent,
                           "received_cents": int(mine.get("received_cents") or 0),
                           "left_cents": budget - spent, "over": spent > budget}
    tasks = tasks if tasks is not None else programme_tasks(tenant_id, project)
    dates = sorted(x for t in tasks for x in (t.get("start_date"), t.get("due_date")) if x)
    if dates:
        first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        span = max((last - first).days + 1, 1)
        out["programme_pct"] = max(0, min(100, round(100 * ((day - first).days + 1) / span)))
        out["days_left"] = max((last - day).days, 0)
    pg = next((s for s in b.get("sections") or [] if s.get("pg_weeks")), None)
    if pg and dates:
        weeks = ((day - date.fromisoformat(dates[0])).days // 7) + 1
        out["pg"] = {"weeks_in": max(weeks, 0), "weeks": pg["pg_weeks"],
                     "over_time": weeks > pg["pg_weeks"], "week_cents": pg.get("pg_week_cents")}
    out["spent_pct"] = round(100 * spent / budget) if budget else None
    out["variations"] = variations_to_flag(tenant_id, project, b.get("baseline_set_at"))
    return out


def variations_to_flag(tenant_id: str, project: str, since: Optional[str]) -> List[Dict[str, Any]]:
    """Cost documents filed for the project since the baseline was signed that change it — labelled
    a variation / additional work (doc_labels) — each needs a variation order."""
    from vula.integrations.doc_labels import is_variation
    try:
        q = (_client().table("vula_filed_documents").select("id,filename,category,summary,fields,created_at")
             .eq("tenant_id", tenant_id).eq("project", project))
        if since:
            q = q.gte("created_at", since)
        rows = q.order("created_at", desc=True).limit(200).execute().data or []
    except Exception as exc:
        log.debug("variation read skipped: %s", exc)
        return []
    out = []
    for r in rows:
        f = r.get("fields") or {}
        if is_variation(f):
            out.append({"id": r["id"], "filename": r.get("filename"),
                        "amount_cents": int(f.get("total_cents") or 0),
                        "vo": f.get("variation_order")})
    return out


# ── The morning messages ─────────────────────────────────────────────────────

def staff_message(name: str, project: str, day: date, tasks: List[Dict[str, Any]]) -> str:
    first = (name or "").split(" ")[0] or "there"
    head = f"Morning {first} 👷 — *{project}*, {day.strftime('%a %d %b')}. Today's tasks:"
    return (f"{head}\n\n{_by_room(tasks)}\n\n"
            "Reply *DONE* with the task when it's finished (a photo helps), or tell me what's holding it up.")


def owner_message(project: str, day: date, plan: Dict[str, List[Dict[str, Any]]],
                  cost: Optional[Dict[str, Any]], needs_number: List[str]) -> str:
    parts = [f"📋 *{project}* — {day.strftime('%a %d %b')}"]
    if cost and cost.get("programme_pct") is not None:
        parts.append(f"Programme: {cost['programme_pct']}% of the time used, {cost['days_left']} day(s) left.")
    parts.append(f"*Today — {len(plan['today'])} task(s)*\n{_by_room(plan['today'], with_who=True)}"
                 if plan["today"] else "*Today* — nothing scheduled on the programme.")
    if plan["overdue"]:
        parts.append(f"⚠️ *Overdue — {len(plan['overdue'])}*\n{_by_room(plan['overdue'], with_who=True)}")
    if cost:
        line = (f"💰 *Cost vs signed baseline*: {_r(cost['spent_cents'])} paid out so far (bank, "
                f"allocated to the project) against the {_r(cost['budget_cents'])} baseline excl. VAT "
                f"({cost['spent_pct']}%) — {_r(cost['left_cents'])} left.")
        if cost.get("over"):
            line += " 🔴 *Over the baseline.*"
        elif cost.get("programme_pct") is not None and cost["spent_pct"] is not None \
                and cost["spent_pct"] > cost["programme_pct"] + 10:
            line += " 🟠 Spending ahead of the programme."
        parts.append(line)
        if cost.get("pg") and cost["pg"]["over_time"]:
            parts.append(f"🟠 P&Gs: week {cost['pg']['weeks_in']} of {cost['pg']['weeks']} priced — "
                         f"every extra week is {_r(cost['pg']['week_cents'] or 0)} not in the baseline.")
        vos = [v for v in cost.get("variations") or [] if not v.get("vo")]
        if vos:
            parts.append("📝 *Needs a variation order* (outside the signed baseline):\n" + "\n".join(
                f"• {v['filename']}" + (f" — {_r(v['amount_cents'])}" if v["amount_cents"] else "")
                for v in vos[:10]))
    if needs_number:
        parts.append("📱 No WhatsApp number yet for: " + ", ".join(needs_number)
                     + ". Send *add staff <name> <number> <trade>* and they'll get their tasks each morning.")
    return "\n\n".join(parts)


def _owners(tenant_id: str) -> List[str]:
    try:
        from vula import team_index
        return [m["whatsapp"] for m in team_index.active_members(tenant_id)
                if (m.get("role") or "") in ("owner", "manager") and m.get("whatsapp")]
    except Exception:
        return []


async def morning_briefs(tenant_id: str, day: Optional[date] = None, send: bool = True) -> Dict[str, Any]:
    """Build (and send) today's messages for every project with programme tasks."""
    day = day or today_sast()
    if send:
        await refresh_from_clickup(tenant_id)          # ClickUp programmes are read fresh each morning
    tasks = programme_tasks(tenant_id)
    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        by_project.setdefault(t["project_id"], []).append(t)
    people = {p["id"]: p for p in _people(tenant_id) if p.get("id")}
    out: Dict[str, Any] = {"day": day.isoformat(), "projects": []}
    from vula.api.whatsapp import _send_reply
    for project, ptasks in by_project.items():
        plan = day_plan(ptasks, day)
        dates = [t.get("due_date") for t in ptasks if t.get("due_date")]
        if not plan["today"] and not plan["overdue"] and dates and max(dates) < (day - timedelta(days=7)).isoformat():
            continue                                                  # programme long finished
        per_person: Dict[str, List[Dict[str, Any]]] = {}
        needs_number = set()
        for t in plan["today"]:
            if t.get("assigned_to") and t["assigned_to"] in people:
                per_person.setdefault(t["assigned_to"], []).append(t)
            elif t.get("assignee_name"):
                needs_number.add(t["assignee_name"])
        cost = cost_position(tenant_id, project, day, ptasks)
        owner_text = owner_message(project, day, plan, cost, sorted(needs_number))
        staff = [{"name": people[pid]["name"], "phone": people[pid]["phone"],
                  "text": staff_message(people[pid]["name"], project, day, ts)}
                 for pid, ts in per_person.items()]
        sent = 0
        if send:
            for s in staff:
                if await _send_reply(s["phone"], s["text"], tenant_id,
                                     idem_key=f"programme:{project}:{day}:{s['phone']}"):
                    sent += 1
            for phone in _owners(tenant_id):
                if await _send_reply(phone, owner_text, tenant_id,
                                     idem_key=f"programme-owner:{project}:{day}:{phone}"):
                    sent += 1
        out["projects"].append({"project": project, "owner": owner_text, "staff": staff,
                                "sent": sent, "cost": cost})
    return out


async def import_and_report(tenant_id: str, project: str, doc: Dict[str, Any],
                            notify: bool = True) -> Dict[str, Any]:
    """Read a filed programme into the project's tasks and tell the owner what was read."""
    tasks = await read_programme(tenant_id, doc)
    if not tasks:
        result = {"project": project, "document": doc.get("filename"), "tasks": 0}
        text = (f"📋 I couldn't read any tasks from *{doc.get('filename')}*. If it's a Gantt image, "
                "send it as a PDF or the Excel export and I'll try again.")
    else:
        result = import_programme(tenant_id, project, doc, tasks)
        text = (f"📋 Programme read for *{result['project']}*: {result['tasks']} task(s), "
                f"{result['start']} to {result['end']}, rooms: {', '.join(result['rooms'])}.\n"
                "Each morning at 06:00 your site team gets their tasks for the day per room, and "
                "you get the day's work, anything overdue and cost against the signed baseline.")
        if result["needs_number"]:
            text += ("\n\n📱 I need a WhatsApp number for: " + ", ".join(result["needs_number"])
                     + ". Send *add staff <name> <number> <trade>* for each.")
    if notify:
        from vula.api.whatsapp import _send_reply
        for phone in _owners(tenant_id):
            await _send_reply(phone, text, tenant_id,
                              idem_key=f"programme-read:{doc.get('id')}:{phone}")
    result["text"] = text
    return result


_ADD_STAFF_RE = re.compile(
    r"^\s*add\s+(?:site\s+)?staff\s+(?P<name>[A-Za-z][A-Za-z .'-]*?)\s+(?P<phone>\+?[\d ]{9,15})"
    r"(?:\s+(?P<trade>[A-Za-z][\w &/-]*))?\s*$", re.IGNORECASE)


def parse_add_staff(text: str) -> Optional[Dict[str, str]]:
    m = _ADD_STAFF_RE.match(text or "")
    if not m:
        return None
    return {"name": m.group("name").strip(), "phone": re.sub(r"\D", "", m.group("phone")),
            "trade": (m.group("trade") or "").strip()}


def active_programme_project(tenant_id: str) -> Optional[str]:
    """The project whose programme is running now (the latest end date that isn't past)."""
    today = today_sast().isoformat()
    ends: Dict[str, str] = {}
    for t in programme_tasks(tenant_id):
        if t.get("due_date"):
            ends[t["project_id"]] = max(ends.get(t["project_id"], ""), t["due_date"])
    live = [p for p, e in ends.items() if e >= today] or list(ends)
    return sorted(live, key=lambda p: ends[p])[-1] if live else None


# ── Programmes that live in ClickUp ──────────────────────────────────────────
# 2026-09-29 (Ian): DIGG runs its programmes in ClickUp — Belladonna's "Work Programme" list
# ("Plumbing First Fix (Edison)", "Painting (Eric)", …); Sporty Phase 2 has one list per room.
# Read straight from there, so there's no Gantt to send and a change in ClickUp reaches the
# next morning's messages.

_PROGRAMME_LIST = re.compile(r"programme|program\b|gantt|schedule of works|work plan", re.IGNORECASE)
_NOT_PROGRAMME = re.compile(r"procurement|documents?|design|concept|legali[sz]ation|council|submission",
                            re.IGNORECASE)
_WHO_IN_TITLE = re.compile(r"\s*\(([^)]+)\)\s*$")


def clickup_programme_lists(tenant_id: str, project: str) -> List[tuple]:
    """(list_id, list_name) of the project's programme in ClickUp: the lists in its folder named
    like a programme ("Work Programme"); failing that, a folder of per-room lists ("Sporty P2 –
    Reception") counts as the programme. Design, procurement and council lists never do."""
    from vula.commerce.service import project_key
    from vula.integrations.doc_filing import _clickup_candidates, _project_label
    mine = [(lid, name) for lid, name in _clickup_candidates(tenant_id)
            if project_key(_project_label(name)) == project_key(project)]
    leaf = lambda n: str(n).split("/")[-1].strip()
    named = [(lid, n) for lid, n in mine if _PROGRAMME_LIST.search(leaf(n)) and not _NOT_PROGRAMME.search(leaf(n))]
    if named:
        return named
    rooms = [(lid, n) for lid, n in mine if not _NOT_PROGRAMME.search(leaf(n))]
    return rooms if len(rooms) >= 3 else []


def _room_from_list(list_name: str) -> Optional[str]:
    """ "Sporty P2 – Reception" → "Reception"; a "Work Programme" list has no room."""
    leaf = str(list_name).split("/")[-1].strip()
    if _PROGRAMME_LIST.search(leaf):
        return None
    parts = re.split(r"\s+[–—-]\s+", leaf)
    return parts[-1].strip() if len(parts) > 1 else None


def tasks_from_clickup(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ClickUp tasks → programme tasks. The person comes from ClickUp's assignees, else the name
    in brackets in the title ("Painting (Eric)"). A task with only a due date starts the day after
    the previous due date in the programme, so a two-week phase shows on every day of it."""
    tasks = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.startswith("📎"):
            continue
        m = _WHO_IN_TITLE.search(name)
        who = ", ".join(r.get("assignees") or []) or (m.group(1) if m else "")
        title = _WHO_IN_TITLE.sub("", name).strip() if m else name
        tasks.append({"task": title, "assignee": who, "room": _room_from_list(r.get("list") or ""),
                      "start": r.get("start_date"), "end": r.get("due_date"),
                      "done": bool(r.get("closed")) or r.get("status") in ("complete", "done", "closed")})
    dues = sorted({t["end"] for t in tasks if t["end"]})
    for t in tasks:
        if t["end"] and not t["start"]:
            earlier = [d for d in dues if d < t["end"]]
            t["start"] = ((date.fromisoformat(earlier[-1]) + timedelta(days=1)).isoformat()
                          if earlier else t["end"])
            t["notes"] = "start inferred from the programme order"
    return tasks


async def import_from_clickup(tenant_id: str, project: str, notify: bool = False) -> Dict[str, Any]:
    from vula.clickup import service as clickup
    project = _canon(tenant_id, project)
    lists = clickup_programme_lists(tenant_id, project)
    if not lists:
        return {"project": project, "tasks": 0, "error": "No programme list for this project in ClickUp."}
    rows: List[Dict[str, Any]] = []
    for lid, _name in lists:
        rows += await clickup.list_programme_tasks(tenant_id, lid)
    tasks = normalise_tasks(tasks_from_clickup(rows), today_sast().year)
    doc = {"id": "clickup:" + ",".join(sorted(l for l, _ in lists)),
           "filename": "ClickUp — " + ", ".join(str(n).split("/")[-1].strip() for _, n in lists)}
    result = import_programme(tenant_id, project, doc, tasks)
    result["open"] = sum(1 for t in tasks if not t.get("done"))
    if notify:
        from vula.api.whatsapp import _send_reply
        text = (f"📋 Programme read from ClickUp for *{result['project']}*: {result['tasks']} task(s), "
                f"{result['start']} to {result['end']}. Each morning at 06:00 your site team gets "
                "their tasks for the day, and you get the day's work and anything overdue.")
        if result["needs_number"]:
            text += ("\n\n📱 I need a WhatsApp number for: " + ", ".join(result["needs_number"])
                     + ". Send *add staff <name> <number> <trade>* for each.")
        for phone in _owners(tenant_id):
            await _send_reply(phone, text, tenant_id, idem_key=f"programme-clickup:{project}:{today_sast()}")
        result["text"] = text
    return result


def running_projects(tenant_id: str) -> List[str]:
    try:
        rows = (_client().table("vula_projects").select("name,status").eq("tenant_id", tenant_id)
                .execute().data or [])
    except Exception as exc:
        log.debug("project register read skipped: %s", exc)
        return []
    return [r["name"] for r in rows if r.get("name") and (r.get("status") or "active") == "active"]


async def refresh_from_clickup(tenant_id: str) -> List[Dict[str, Any]]:
    """Before the morning messages: re-read every active project's ClickUp programme, so what's
    done or moved in ClickUp is what the site team is told."""
    out = []
    # Only programmes the owner switched on ("programme Belladonna") — a project with old ClickUp
    # lists (HPC's phases, Sporty's rooms) doesn't start sending overdue lists by itself.
    live = {t["project_id"] for t in programme_tasks(tenant_id)
            if str(t.get("source_doc_id") or "").startswith("clickup:")}
    for project in sorted(live):
        try:
            out.append(await import_from_clickup(tenant_id, project))
        except Exception as exc:
            log.warning("ClickUp programme refresh failed for %s/%s: %s", tenant_id, project, exc)
    return out


_START_PROGRAMME_RE = re.compile(
    r"^\s*(?:start|run|load|read|sync)?\s*(?:the\s+)?(?:programme|program|gantt)\s+(?:for\s+)?(?P<project>.{2,60}?)"
    r"(?:\s+from\s+click\s?up)?\s*[.!]*\s*$", re.IGNORECASE)


def parse_start_programme(text: str, tenant_id: str) -> Optional[str]:
    """ "programme Belladonna" / "start programme for Belladonna from ClickUp" → the project."""
    m = _START_PROGRAMME_RE.match(text or "")
    if not m:
        return None
    from vula.commerce.service import project_key
    want = project_key(m.group("project"))
    return next((p for p in running_projects(tenant_id) if project_key(p) == want), None)
