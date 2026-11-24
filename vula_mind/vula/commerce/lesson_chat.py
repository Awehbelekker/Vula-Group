"""Asking for a lesson or for gear in the WhatsApp chat (24 Nov 2026) — vula/commerce/lessons.py
keeps the request and prices it.

    "surf lesson"       → which lesson? → your level → group or private → how many people →
                          one lesson or a package → wetsuits? → which day → what time of day →
                          the request, with its quote: "we'll confirm the time shortly"
    "hire a board"      → which gear? → for how long → how many → which day → the request
    "how much is a lesson" → the business's own price list, and a way to ask for one
    "my lesson" / "cancel my lesson" → their own requests still to come → cancelled

Every step is an exact id from a list or a button, carrying what was chosen so far: no model reads a
lesson, a number of people or a day, so nothing is asked for that the customer didn't tap. The
price is the price list's, worked out in code. Nothing is booked here: a person at the business
confirms the time and the coach. A first-time customer is asked their name once (open question
`lesson_name`).

Only a business with requests switched on takes these messages.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Dict, List

log = logging.getLogger(__name__)

NAME_QUESTION = "lesson_name"
_LESSON_RE = re.compile(r"\b(lessons?|lesse|learn(?:ing)? to (?:surf|kite|kitesurf|sup|skate|paddle)|coaching|surf school)\b", re.I)
_HIRE_RE = re.compile(r"\b(hire|hiring|rent|rental|rentals|renting|huur)\b", re.I)
_PRICE_RE = re.compile(r"\b(how much|price|prices|pricing|cost|costs|rates?|hoeveel|prys|pryse)\b", re.I)
_OWN_RE = re.compile(r"\b(cancel|kanselleer|can'?t make|change|move)\b.*\b(lesson|booking|hire|rental|request)\b"
                     r"|\bmy (lesson|lessons|request|rental|hire)\b", re.I)
_LEVEL_CODES = {"ft": "first_timer", "bg": "beginner", "im": "intermediate", "ad": "advanced"}
_PART_CODES = {"m": "morning", "d": "midday", "a": "afternoon", "x": "any"}


def is_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 160 and bool(_LESSON_RE.search(t) or _HIRE_RE.search(t) or _OWN_RE.search(t))


async def _wa(tenant_id: str):
    from vula.api import whatsapp as wa
    return wa, await wa._resolve_wa(tenant_id)


async def _say(phone: str, tenant_id: str, body: str) -> None:
    from vula.api import whatsapp as wa
    await wa._send_reply(phone, body, tenant_id=tenant_id)


async def _list(phone: str, tenant_id: str, header: str, body: str, button: str, rows: List[dict]) -> None:
    wa, creds = await _wa(tenant_id)
    if not (creds and await wa._send_wa_list(creds, wa._wa_number(phone), header[:60], body[:1024], "Tap to choose",
                                             button[:20], [{"title": header[:24], "rows": rows[:10]}])):
        await _say(phone, tenant_id, body + "\n\nSend us a message and we'll help you from here.")


async def _buttons(phone: str, tenant_id: str, body: str, buttons: List[dict]) -> None:
    wa, creds = await _wa(tenant_id)
    if not (creds and await wa._send_wa_buttons(creds, wa._wa_number(phone), body[:1024], buttons[:3])):
        await _say(phone, tenant_id, body)


async def start(phone: str, tenant_id: str, text: str) -> bool:
    """A message about a lesson or about hiring gear. True when this took it."""
    from vula.commerce import lessons
    if not is_request(text):
        return False
    st = lessons.settings(tenant_id)
    if not st["enabled"]:
        return False
    if _OWN_RE.search(text):
        return await show_own(phone, tenant_id)
    wants_hire = bool(_HIRE_RE.search(text)) and not _LESSON_RE.search(text)
    if wants_hire and not st["hire"]:
        return False
    if not wants_hire and not st["lessons"]:
        return False
    if _PRICE_RE.search(text):
        buttons = ([{"id": "lsn_start", "title": "Book a lesson"}] if st["lessons"] else []) \
            + ([{"id": "lsn_h", "title": "Hire gear"}] if st["hire"] else [])
        await _buttons(phone, tenant_id, lessons.price_list_text(tenant_id, st)[:1000], buttons)
        return True
    return await (ask_gear(phone, tenant_id) if wants_hire else ask_lesson(phone, tenant_id))


# ── a lesson ──────────────────────────────────────────────────────────────────────────────────
async def ask_lesson(phone: str, tenant_id: str) -> bool:
    from vula.commerce import lessons
    st = lessons.settings(tenant_id)
    rows = [{"id": f"lsn_l_{x['id']}", "title": x["name"][:24], "description": (x.get("about") or "")[:72]}
            for x in st["lessons"]]
    if st["hire"]:
        rows.append({"id": "lsn_h", "title": "Hire gear", "description": ", ".join(g["name"] for g in st["hire"])[:72]})
    await _list(phone, tenant_id, "Lessons", "What would you like to do?", "Choose", rows)
    return True


async def ask_level(phone: str, tenant_id: str, lid: str) -> bool:
    rows = [{"id": f"lsn_v_{lid}_ft", "title": "First timer"},
            {"id": f"lsn_v_{lid}_bg", "title": "Beginner", "description": "Done it once or twice"},
            {"id": f"lsn_v_{lid}_im", "title": "Intermediate", "description": "Getting the feeling, want the next steps"},
            {"id": f"lsn_v_{lid}_ad", "title": "Advanced", "description": "Looking to progress"}]
    await _list(phone, tenant_id, "Your level", "How much have you done before?", "Choose", rows)
    return True


async def ask_format(phone: str, tenant_id: str, lid: str, lv: str) -> bool:
    from vula.commerce import lessons
    ls = lessons.lesson(lessons.settings(tenant_id), lid)
    if not ls:
        return False
    if not ls["group"] or not ls.get("private"):
        return await ask_people(phone, tenant_id, lid, lv, "g" if ls["group"] else "p")
    cheapest = min(t["single_cents"] for t in ls["group"])
    body = (f"*Group*: with others joining, from {lessons.rand(cheapest)} per person "
            f"(at most {ls['max_per_coach']} per coach).\n"
            f"*Private*: one on one, {lessons.rand(ls['private']['single_cents'])}.")
    await _buttons(phone, tenant_id, body, [{"id": f"lsn_f_{lid}_{lv}_g", "title": "Group"},
                                            {"id": f"lsn_f_{lid}_{lv}_p", "title": "Private"}])
    return True


async def ask_people(phone: str, tenant_id: str, lid: str, lv: str, f: str) -> bool:
    rows = [{"id": f"lsn_n_{lid}_{lv}_{f}_{n}", "title": "Just me" if n == 1 else f"{n} people"} for n in range(1, 9)]
    await _list(phone, tenant_id, "How many?", "How many people are coming?\n\nMore than 8? Send us a message and we'll arrange it.",
                "Choose", rows)
    return True


async def ask_sessions(phone: str, tenant_id: str, lid: str, lv: str, f: str, n: int) -> bool:
    from vula.commerce import lessons
    st = lessons.settings(tenant_id)
    ls = lessons.lesson(st, lid)
    if not ls:
        return False
    fmt = "group" if f == "g" else "private"
    one, _, _ = lessons.quote_lesson(st, lid, fmt, n, False)
    pack, _, sessions = lessons.quote_lesson(st, lid, fmt, n, True)
    if pack is None:
        return await ask_extras(phone, tenant_id, lid, lv, f, n, 1)
    each = " in total" if n > 1 else ""
    body = (f"*One lesson*: {lessons.rand(one)}{each}.\n" if one is not None else "*One lesson*.\n") \
        + f"*{sessions} lessons*: {lessons.rand(pack)}{each}, used whenever it suits you."
    await _buttons(phone, tenant_id, body, [{"id": f"lsn_s_{lid}_{lv}_{f}_{n}_1", "title": "One lesson"},
                                            {"id": f"lsn_s_{lid}_{lv}_{f}_{n}_k", "title": f"{sessions} lessons"}])
    return True


async def ask_extras(phone: str, tenant_id: str, lid: str, lv: str, f: str, n: int, k: int) -> bool:
    """The lesson's first extra (a wetsuit): for everyone, or not."""
    from vula.commerce import lessons
    ls = lessons.lesson(lessons.settings(tenant_id), lid)
    if not ls:
        return False
    sk = "k" if k > 1 else "1"
    if not ls.get("extras"):
        return await ask_day(phone, tenant_id, f"l_{lid}_{lv}_{f}_{n}_{sk}_0")
    e = ls["extras"][0]
    body = f"Do you need a {e['name'].lower()}? {lessons.rand(e['cents'])} per person per lesson."
    await _buttons(phone, tenant_id, body, [{"id": f"lsn_x_{lid}_{lv}_{f}_{n}_{sk}_1", "title": "Yes please" if n == 1 else "Yes, for everyone"},
                                            {"id": f"lsn_x_{lid}_{lv}_{f}_{n}_{sk}_0", "title": "No thanks"}])
    return True


# ── gear ──────────────────────────────────────────────────────────────────────────────────────
async def ask_gear(phone: str, tenant_id: str) -> bool:
    from vula.commerce import lessons
    st = lessons.settings(tenant_id)
    if not st["hire"]:
        return False
    rows = [{"id": f"lsn_g_{g['id']}", "title": g["name"][:24],
             "description": ("from " + lessons.rand(min(g["prices"].values())))[:72]} for g in st["hire"]]
    await _list(phone, tenant_id, "Hire", "What would you like to hire?", "Choose", rows)
    return True


async def ask_duration(phone: str, tenant_id: str, gid: str) -> bool:
    from vula.commerce import lessons
    st = lessons.settings(tenant_id)
    g = lessons.gear(st, gid)
    if not g:
        return False
    rows = []
    for d in lessons.hire_durations(g):
        cents, _ = lessons.quote_hire(st, gid, d, 1)
        rows.append({"id": f"lsn_t_{gid}_{d}", "title": lessons.DURATIONS[d], "description": lessons.rand(cents) + " each"})
    await _list(phone, tenant_id, "How long?", f"How long do you need the {g['name'].lower()}?", "Choose", rows)
    return True


async def ask_count(phone: str, tenant_id: str, gid: str, d: str) -> bool:
    rows = [{"id": f"lsn_q_{gid}_{d}_{n}", "title": str(n)} for n in range(1, 7)]
    await _list(phone, tenant_id, "How many?", "How many do you need?", "Choose", rows)
    return True


# ── the day, the time of day, the name ────────────────────────────────────────────────────────
async def ask_day(phone: str, tenant_id: str, state: str) -> bool:
    """`state` is everything chosen so far: l_<lesson>_<level>_<g|p>_<people>_<1|k>_<extras 0|1>
    or h_<gear>_<duration>_<count>."""
    from vula.commerce import lessons
    today = lessons._now().astimezone(lessons.SAST).date()
    rows = []
    for i in range(10):
        d = today + timedelta(days=i)
        label = "Today" if i == 0 else "Tomorrow" if i == 1 else f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"
        rows.append({"id": f"lsn_d_{state}_{d.isoformat()}", "title": label[:24]})
    await _list(phone, tenant_id, "Which day?", "Which day would you like?\n\nFurther ahead? Send us the date.", "Choose a day", rows)
    return True


async def ask_part(phone: str, tenant_id: str, state: str, day: str) -> bool:
    rows = [{"id": f"lsn_p_{state}_{day}_m", "title": "Morning"}, {"id": f"lsn_p_{state}_{day}_d", "title": "Midday"},
            {"id": f"lsn_p_{state}_{day}_a", "title": "Afternoon"},
            {"id": f"lsn_p_{state}_{day}_x", "title": "Any time", "description": "Whenever the conditions are best"}]
    await _list(phone, tenant_id, "What time of day?", "What time of day suits you? We'll confirm the exact time.", "Choose", rows)
    return True


def _parse(state: str) -> Dict[str, Any]:
    """The request the ids describe; {} when they don't describe one."""
    p = state.split("_")
    if len(p) < 6 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p[-2]):
        return {}
    try:
        if p[0] == "l" and len(p) == 9 and p[2] in _LEVEL_CODES and p[3] in ("g", "p") and p[5] in ("1", "k") \
                and p[6] in ("0", "1") and p[8] in _PART_CODES:
            return {"kind": "lesson", "item_id": p[1], "level": _LEVEL_CODES[p[2]], "fmt": "group" if p[3] == "g" else "private",
                    "people": int(p[4]), "package": p[5] == "k", "all_extras": p[6] == "1", "wanted_date": p[7],
                    "wanted_part": _PART_CODES[p[8]]}
        if p[0] == "h" and len(p) == 6 and p[5] in _PART_CODES:
            return {"kind": "hire", "item_id": p[1], "duration": p[2], "people": int(p[3]), "wanted_date": p[4],
                    "wanted_part": _PART_CODES[p[5]]}
    except ValueError:
        pass
    return {}


async def choose_part(phone: str, tenant_id: str, full_state: str) -> bool:
    from vula.commerce import lessons
    if not _parse(full_state):
        return False
    name = lessons.known_name(tenant_id, phone)
    if not name:
        try:
            from vula.commerce import service
            name = (((await service.get_customer_profile(tenant_id, phone)) or {}).get("name") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
    if name:
        return await place(phone, tenant_id, full_state, name)
    from vula import open_questions as oq
    oq.ask(tenant_id, phone, NAME_QUESTION, full_state, "name for the request")
    await _say(phone, tenant_id, "What name should we put this under?")
    return True


async def take_name(phone: str, tenant_id: str, text: str, question: Dict[str, Any]) -> bool:
    from vula.commerce.event_chat import clean_name
    name = clean_name(text)
    if not name:
        return False
    from vula import open_questions as oq
    oq.close(question["id"], name)
    return await place(phone, tenant_id, str(question.get("ref_id") or ""), name)


async def place(phone: str, tenant_id: str, full_state: str, name: str) -> bool:
    from vula.api import tenants
    from vula.commerce import lessons
    want = _parse(full_state)
    if not want:
        return False
    try:
        if len(lessons.upcoming_for(tenant_id, phone)) >= 5:
            raise lessons.LessonError("You already have five requests open. Reply *my lesson* to see them.")
        extras = None
        if want["kind"] == "lesson" and want.pop("all_extras", False):
            ls = lessons.lesson(lessons.settings(tenant_id), want["item_id"]) or {}
            first = (ls.get("extras") or [None])[0]
            extras = {first["id"]: want["people"]} if first else None
        want.pop("all_extras", None)
        r = await lessons.request(tenant_id, name=name, phone=phone, channel="whatsapp", extras=extras, **want)
    except lessons.LessonError as exc:
        await _say(phone, tenant_id, f"{exc} Reply *lesson* to start again.")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("lesson request in chat failed for %s: %s", tenant_id, exc)
        await _say(phone, tenant_id, "Sorry — that didn't go through. Please try again in a moment.")
        return True
    lines = [f"🤙 Got it, {r['customer_name'].split(' ')[0]}. Your request:", f"*{r['summary']}*", f"{r['wanted'].capitalize()}"]
    for ln in r.get("quote_lines") or []:
        lines.append(f"• {ln['quantity']} × {ln['description']}: {lessons.rand(ln['quantity'] * ln['unit_price_cents'])}")
    lines.append(f"*Total {r['amount']}*" if r["amount"] else "We'll send you the price with the confirmation.")
    lines.append(f"\nThis isn't booked yet. {tenants.display_name(tenant_id)} will confirm the exact time with you here. "
                 f"Reference {r['ref']}.")
    await _say(phone, tenant_id, "\n".join(lines))
    return True


async def show_own(phone: str, tenant_id: str) -> bool:
    from vula.commerce import lessons
    mine = lessons.upcoming_for(tenant_id, phone)
    if not mine:
        await _say(phone, tenant_id, "You have nothing booked with us at the moment. Reply *lesson* to ask for one.")
        return True

    def line(r: dict) -> str:
        return f"{r['summary']}, " + (r["when"] if r["when"] else f"{r['wanted']} (waiting to be confirmed)")
    rows = [{"id": f"lsn_c_{r['id']}", "title": f"Cancel {r['ref']}"[:24], "description": line(r)[:72]} for r in mine]
    body = "Yours:\n" + "\n".join(f"• {line(r)} ({r['ref']})" for r in mine) + "\n\nTap one to cancel it."
    await _list(phone, tenant_id, "Your lessons", body, "Cancel one", rows)
    return True


async def handle_tap(phone: str, tenant_id: str, reply_id: str) -> bool:
    """A tap on one of this module's rows or buttons (ids starting lsn_)."""
    from vula.commerce import lessons
    if not lessons.settings(tenant_id)["enabled"]:
        return False
    if reply_id == "lsn_start":
        return await ask_lesson(phone, tenant_id)
    if reply_id == "lsn_h":
        return await ask_gear(phone, tenant_id)
    kind, _, rest = reply_id[4:].partition("_")
    p = rest.split("_") if rest else []
    if kind == "l" and len(p) == 1:
        return await ask_level(phone, tenant_id, p[0])
    if kind == "v" and len(p) == 2 and p[1] in _LEVEL_CODES:
        return await ask_format(phone, tenant_id, p[0], p[1])
    if kind == "f" and len(p) == 3 and p[2] in ("g", "p"):
        return await ask_people(phone, tenant_id, p[0], p[1], p[2])
    if kind == "n" and len(p) == 4 and p[3].isdigit() and 1 <= int(p[3]) <= 8:
        return await ask_sessions(phone, tenant_id, p[0], p[1], p[2], int(p[3]))
    if kind == "s" and len(p) == 5 and p[3].isdigit() and p[4] in ("1", "k"):
        return await ask_extras(phone, tenant_id, p[0], p[1], p[2], int(p[3]), 2 if p[4] == "k" else 1)
    if kind == "x" and len(p) == 6 and p[5] in ("0", "1"):
        return await ask_day(phone, tenant_id, "l_" + "_".join(p))
    if kind == "g" and len(p) == 1:
        return await ask_duration(phone, tenant_id, p[0])
    if kind == "t" and len(p) == 2 and p[1] in lessons.DURATIONS:
        return await ask_count(phone, tenant_id, p[0], p[1])
    if kind == "q" and len(p) == 3 and p[2].isdigit():
        return await ask_day(phone, tenant_id, "h_" + "_".join(p))
    if kind == "d" and len(p) >= 4 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p[-1]):
        return await ask_part(phone, tenant_id, "_".join(p[:-1]), p[-1])
    if kind == "p" and len(p) >= 5:
        return await choose_part(phone, tenant_id, "_".join(p))
    if kind == "c" and len(p) == 1:
        done = await lessons.cancel_own(tenant_id, phone, p[0])
        await _say(phone, tenant_id, f"Cancelled: {done['summary']} ({done['ref']})." if done
                   else "I couldn't find that under your number. It may already be cancelled.")
        return True
    return False
