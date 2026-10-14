"""
vula/commerce/costing_setup.py — "set up costing": one question at a time on WhatsApp.

14 Oct 2026 (Ian: "the questions Vula will ask the tenant to process it accurately"). The same
shape as the business-profile interview (business_profile.handle_interview): the owner sends
*set up costing*, Vula asks, the owner answers in their own words, *skip* moves on, *stop* ends
it. Every answer that changes something is shown back first and saved only on *yes*, then read
back (vula/commerce/manufacturing.py does the saving). Vula proposes what it already knows —
prices from past supplier invoices, labour and running costs from the bank — and the owner
confirms or corrects; nothing is set on a guess.

    1. products   which product to cost, then what goes into one batch, how many good ones it
                  makes and the labour hours ("18 kg concrete mix, 150 g pigment — makes 1, 1.5 h")
    2. prices     every material in those recipes with no price: the matching invoice items, or
                  what the owner pays ("R95 for a 25 kg bag")
    3. labour     the bank's wages per month over the team's hours a week → a rate per hour
    4. overhead   the bank's running costs per month, spread per labour hour / batch / unit
    5. the cost sheets

Parsing is deterministic (no model): quantities, units, money and counts are read with patterns
and checked by the costing engine, which refuses a unit that doesn't fit a material.
"""
from __future__ import annotations

import logging
import re
import time
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from vula.commerce import costing
from vula.commerce import costing_history as hist
from vula.commerce.costing import CostingError, rands

log = logging.getLogger(__name__)

_ACTIVE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_TTL = 1800

_START_RE = re.compile(r"^\s*(?:(?:set ?up|start|do|update|finish)\s+(?:my |our |the )?costing|costing set ?up|"
                       r"set ?up (?:my |our |the )?(?:product )?costs?)\s*[.!]?\s*$", re.I)
_SKIP_RE = re.compile(r"^\s*(?:skip|next|pass|later|not sure|n/?a|none)\s*[.!]?\s*$", re.I)
_STOP_RE = re.compile(r"^\s*(?:stop|cancel|quit|that'?s all|enough)\s*[.!]?\s*$", re.I)
_YES_RE = re.compile(r"^\s*(?:y|yes|yep|yeah|ok|okay|sure|correct|save|use it|go ahead|confirm)\b", re.I)
_NO_RE = re.compile(r"^\s*(?:n|no|nope|wrong)\b", re.I)
_DONE_RE = re.compile(r"^\s*(?:done|no more|that'?s it|finished|next)\s*[.!]?\s*$", re.I)

_NUM = r"\d+(?:[.,]\d+)?"
_MONEY = re.compile(r"R\s*(\d{1,3}(?:[ ,]\d{3})+(?:\.\d{1,2})?(?!\d)|\d+(?:[.,]\d{1,2})?(?!\d))"
                    r"|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rand|zar)\b", re.I)


def _num(text: str) -> Optional[Decimal]:
    m = re.search(_NUM, text or "")
    return Decimal(m.group(0).replace(",", ".")) if m else None


def parse_money(text: str) -> Optional[int]:
    """'R18,400' / 'R 18 400' / 'R95.50' / '950 rand' → cents. A bare number counts as rands."""
    m = _MONEY.search(text or "")
    raw = (m.group(1) or m.group(2)) if m else None
    if raw is None:
        bare = re.fullmatch(r"\s*(\d[\d\s,]*(?:\.\d{1,2})?)\s*", text or "")
        raw = bare.group(1) if bare else None
    if raw is None:
        return None
    raw = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        raw = raw.replace(",", "")
    raw = raw.replace(",", ".")
    try:
        return int((Decimal(raw) * 100).quantize(Decimal(1)))
    except Exception:
        return None


def _unit_token(tok: str) -> Optional[str]:
    t = (tok or "").lower().rstrip(".")
    if t in costing.UNITS and t not in ("", "x"):
        return t
    if t in costing.PACK_WORDS:
        return t
    return None


def parse_recipe(text: str) -> Dict[str, Any]:
    """'18 kg concrete mix, 150 g pigment and 0.25 l sealer — makes 1, 1.5 hours'
    → {ingredients: [{material, qty, unit}], makes, hours}."""
    out: Dict[str, Any] = {"ingredients": [], "makes": None, "hours": None}
    t = text or ""
    m = re.search(r"\b(?:makes|gives|yields?|=)\s*(" + _NUM + r")", t, re.I) or \
        re.search(r"(" + _NUM + r")\s*(?:per batch|a batch|good ones?)\b", t, re.I)
    if m:
        out["makes"] = Decimal(m.group(1).replace(",", "."))
        t = t[:m.start()] + " , " + t[m.end():]
    h = re.search(r"(" + _NUM + r")\s*(?:hours?|hrs?|h)\b", t, re.I)
    if h:
        out["hours"] = Decimal(h.group(1).replace(",", "."))
        t = t[:h.start()] + " , " + t[h.end():]
    for part in re.split(r"(?<!\d),|,(?!\d)|[;\n+]|\s+and\s+|\s+[-–—]\s+", t):
        part = part.strip(" .:-–—")
        if not part or not re.search(r"[a-z]", part, re.I):
            continue
        a = re.match(r"^(" + _NUM + r")\s*([a-zA-Z²]+)?\.?\s*(?:of\s+)?(.*)$", part)
        b = re.match(r"^(.*?[a-zA-Z].*?)\s*[:=]?\s+(" + _NUM + r")\s*([a-zA-Z²]+)?$", part)
        if a:
            qty, tok, rest = a.group(1), a.group(2) or "", a.group(3)
            unit = _unit_token(tok) or ("each" if tok.lower() == "x" else None)
            name = rest if unit else f"{tok} {rest}"
        elif b:
            name, qty, tok = b.group(1), b.group(2), b.group(3) or ""
            unit = _unit_token(tok)
            if tok and not unit:
                continue
        else:
            continue
        name = " ".join(name.split()).strip(" .")
        if not name:
            continue
        out["ingredients"].append({"material": name, "qty": float(Decimal(qty.replace(",", "."))),
                                   "unit": unit or "each"})
    return out


def parse_price(text: str) -> Optional[Dict[str, Any]]:
    """'R95 for a 25 kg bag' → {total_cents 9500, qty 25, unit kg, pack (25, kg)};
    'R190 for 2 bags' → qty 2, unit bag; 'R3.80 per kg' / 'R3.80/kg' → qty 1 kg; 'R12 each'."""
    cents = parse_money(text)
    if cents is None:
        return None
    m = _MONEY.search(text or "")
    rest = (text or "")[m.end():] if m else ""
    q = re.search(r"(" + _NUM + r")\s*([a-zA-Z²]+)(?:\s+(" + "|".join(sorted(costing.PACK_WORDS)) + r"))?\b",
                  rest, re.I)
    if q and _unit_token(q.group(2)):
        qty, unit = Decimal(q.group(1).replace(",", ".")), q.group(2).lower()
        pack = (qty, unit) if q.group(3) and unit not in costing.PACK_WORDS else None
        return {"total_cents": cents, "qty": qty, "unit": unit, "pack": pack}
    w = re.search(r"\b([a-zA-Z²]+)\b", re.sub(r"\b(for|per|a|an|the|one)\b", " ", rest, flags=re.I))
    if w and _unit_token(w.group(1)):
        return {"total_cents": cents, "qty": Decimal(1), "unit": w.group(1).lower(), "pack": None}
    if re.search(r"/\s*([a-zA-Z²]+)", rest) and _unit_token(re.search(r"/\s*([a-zA-Z²]+)", rest).group(1)):
        return {"total_cents": cents, "qty": Decimal(1),
                "unit": re.search(r"/\s*([a-zA-Z²]+)", rest).group(1).lower(), "pack": None}
    return {"total_cents": cents, "qty": None, "unit": None, "pack": None}


def parse_hours_week(text: str) -> Optional[Decimal]:
    """'2 people, 40 hours each' → 80; '80 hours' → 80; '3 x 45' → 135."""
    t = text or ""
    ppl = re.search(r"(" + _NUM + r")\s*(?:people|persons?|workers?|staff|guys|men|ladies|of us|employees)", t, re.I)
    hrs = re.search(r"(" + _NUM + r")\s*(?:hours?|hrs?|h)\b", t, re.I)
    mult = re.fullmatch(r"\s*(" + _NUM + r")\s*[x×*]\s*(" + _NUM + r")\s*", t)
    if mult:
        return Decimal(mult.group(1).replace(",", ".")) * Decimal(mult.group(2).replace(",", "."))
    if hrs:
        h = Decimal(hrs.group(1).replace(",", "."))
        return h * Decimal(ppl.group(1).replace(",", ".")) if ppl else h
    n = _num(t)
    return n if n and n > 0 else None


def parse_rate(text: str) -> Optional[int]:
    """'R95 an hour' → 9500 per hour; 'R400 a day' → 5000 (a day is 8 hours)."""
    cents = parse_money(text)
    if cents is None:
        return None
    if re.search(r"\b(day|daily|shift)\b", text or "", re.I):
        return int((Decimal(cents) / 8).quantize(Decimal(1)))
    return cents


def _ask_product() -> str:
    return ("Which product shall we cost? Send its name as it is in your products "
            "(e.g. *Round Drop-In Basin*) — or *next* to skip to prices and labour.")


def _ask_recipe(name: str) -> str:
    return (f"What goes into ONE batch of *{name}*, how many good ones does it make, and how many "
            "labour hours (everyone together)?\n"
            "e.g. \"18 kg concrete mix, 150 g pigment, 0.25 l sealer — makes 1, 1.5 hours\"")


def _modules_ok(tenant_id: str) -> bool:
    try:
        from vula.api.tenants import tenant_profile
        return "manufacturing" in (tenant_profile(tenant_id).get("modules") or [])
    except Exception:
        return False


def _footer() -> str:
    return "\n\n(*skip* moves on, *stop* finishes for now.)"


async def handle(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """The reply if this message is part of the costing setup, else None."""
    key = (tenant_id, phone)
    now = time.time()
    st = _ACTIVE.get(key)
    if st and now - st["t"] > _TTL:
        _ACTIVE.pop(key, None)
        st = None
    if not st:
        if not _START_RE.search(text or ""):
            return None
        if not _modules_ok(tenant_id):
            return ("Costing is part of the Manufacturing module, which isn't switched on for this "
                    "business yet — ask Vula to switch it on.")
        st = {"step": "product", "t": now, "products": [], "data": {}}
        _ACTIVE[key] = st
        return ("Let's work out what each product costs you to make. I'll use what's on your supplier "
                "invoices and bank statements where I can, and ask you for the rest — one question at "
                "a time.\n\n" + _ask_product() + _footer())
    st["t"] = now
    if _STOP_RE.search(text or ""):
        _ACTIVE.pop(key, None)
        return "Saved what we did. Send *set up costing* any time to carry on."
    try:
        reply = await _step(tenant_id, phone, st, (text or "").strip())
    except CostingError as exc:
        reply = f"{exc}\n\nTry again, or *skip*."
    except Exception as exc:                       # never leave the owner without an answer
        log.warning("costing setup step %s failed: %s", st.get("step"), exc)
        _ACTIVE.pop(key, None)
        reply = "Something went wrong saving that — nothing more was changed. Send *set up costing* to carry on."
    if st.get("step") == "end":
        _ACTIVE.pop(key, None)
    return reply


async def _step(tenant_id: str, phone: str, st: Dict[str, Any], text: str) -> str:
    from vula.commerce import manufacturing as mfg
    step = st["step"]
    skip = bool(_SKIP_RE.search(text))

    # 1. products and recipes
    if step == "product":
        if skip or _DONE_RE.search(text):
            return _start_prices(tenant_id, st)
        found = await mfg.find_product(tenant_id, text)
        if found.get("candidates"):
            return "Which one? " + "; ".join(found["candidates"]) + _footer()
        if not found.get("product"):
            return f"I can't find a product called \"{text}\". Send the name as it is in your products, or *next*."
        p = found["product"]
        st["product"] = {"id": p["id"], "name": p["name"]}
        current = costing.active_bom(tenant_id, product_id=p["id"])
        st["step"] = "recipe"
        if current:
            return (f"*{p['name']}* already has a recipe (version {current['version']}). Send the new one to "
                    "replace it, or *keep*.\n\n" + _ask_recipe(p["name"]))
        return _ask_recipe(p["name"]) + _footer()

    if step == "recipe":
        if skip or re.fullmatch(r"\s*keep\s*", text, re.I):
            st["step"] = "product"
            return "OK. Another product? Send its name — or *next* for prices and labour."
        r = parse_recipe(text)
        if not r["ingredients"]:
            return ("I couldn't read the materials — write each as amount, unit and name, e.g. "
                    "\"18 kg concrete mix, 150 g pigment\".")
        st["recipe"] = r
        if r["makes"] is None:
            st["step"] = "makes"
            return f"How many good *{st['product']['name']}* does one batch give?"
        if r["hours"] is None:
            st["step"] = "hours"
            return "And how many labour hours does one batch take (everyone together)? *0* if you don't count it."
        return await _recipe_preview(tenant_id, st)

    if step == "makes":
        n = _num(text)
        if not n or n <= 0:
            return "Send a number — how many good ones from one batch?"
        st["recipe"]["makes"] = n
        if st["recipe"]["hours"] is None:
            st["step"] = "hours"
            return "And how many labour hours does one batch take (everyone together)? *0* if you don't count it."
        return await _recipe_preview(tenant_id, st)

    if step == "hours":
        n = _num(text)
        if n is None:
            return "Send the hours as a number, e.g. *1.5*."
        st["recipe"]["hours"] = n
        return await _recipe_preview(tenant_id, st)

    if step == "recipe_confirm":
        if _YES_RE.search(text):
            r = st["recipe"]
            res = await mfg.set_recipe(tenant_id, "", r["ingredients"], makes=r["makes"],
                                       labour_hours=r["hours"], product_id=st["product"]["id"],
                                       actor=phone, confirm=True)
            if res.get("error"):
                st["step"] = "recipe"
                return f"{res['error']}\n\n" + _ask_recipe(st["product"]["name"])
            st["products"].append(st["product"])
            st["step"] = "product"
            return (f"✅ Recipe saved for *{st['product']['name']}*.\n\nAnother product? Send its name "
                    "— or *next* for prices and labour.")
        if _NO_RE.search(text):
            st["step"] = "recipe"
            return "No problem — send it again.\n\n" + _ask_recipe(st["product"]["name"])
        r = parse_recipe(text)
        if r["ingredients"]:
            st["recipe"] = {**st["recipe"], **{k: v for k, v in r.items() if v}}
            return await _recipe_preview(tenant_id, st)
        return "Reply *yes* to save it, or *no* to change it."

    # 2. material prices
    if step == "price":
        mat = st["queue"][0]
        sug = st.get("sug") or []
        chosen = None
        if skip:
            return _next_price(tenant_id, st, f"Skipped {mat['name']} — it stays unpriced for now.")
        if sug and (_YES_RE.search(text) or re.fullmatch(r"\s*\d\s*", text)):
            i = int(text.strip()) if re.fullmatch(r"\s*\d\s*", text) else 1
            if not 1 <= i <= len(sug):
                return f"Pick 1 to {len(sug)}, or tell me what you pay."
            chosen = mfg.price_material(tenant_id, mat["name"], pick=i, actor=phone, confirm=True)
        else:
            p = parse_price(text)
            if not p:
                return f"What do you pay for {mat['name']}? e.g. \"R95 for a 25 kg bag\" — or *skip*."
            if p["qty"] is None:
                return (f"R{p['total_cents'] / 100:,.2f} for how much {mat['name']}? "
                        "e.g. \"R95 for a 25 kg bag\" or \"R3.80 per kg\".")
            m = costing.find_material(tenant_id, mat["name"]) or mat
            if p.get("pack") and not m.get("pack_size"):
                try:
                    if costing.base_unit_for(p["pack"][1]) == m.get("base_unit"):
                        costing._db().table("commerce_materials").update(
                            {"pack_size": float(p["pack"][0]), "pack_unit": p["pack"][1]}) \
                            .eq("tenant_id", tenant_id).eq("id", m["id"]).execute()
                except CostingError:
                    pass
            chosen = mfg.price_material(tenant_id, mat["name"], total_cents=p["total_cents"], qty=p["qty"],
                                        unit=p["unit"], actor=phone, confirm=True)
        if chosen.get("error"):
            return f"{chosen['error']}\nTry again, or *skip*."
        return _next_price(tenant_id, st, chosen.get("reply_verbatim") or "✅ Saved.")

    # 3. labour
    if step == "labour_hours":
        if skip:
            return _ask_labour_rate(st, "")
        h = parse_hours_week(text)
        if not h or h <= 0:
            return "How many hours a week, all together? e.g. \"2 people, 40 hours each\"."
        st["data"]["hours_week"] = h
        rate = hist.per(st["bank"]["labour_monthly_cents"], hist.hours_per_month(h))
        st["data"]["labour_suggest"] = rate
        st["step"] = "labour_rate"
        return (f"That's about {rands(rate)} per labour hour ({rands(st['bank']['labour_monthly_cents'])} a "
                f"month ÷ {costing._n(hist.hours_per_month(h))} hours). Use it? *yes* — or send your own rate, "
                "e.g. \"R95 an hour\".")

    if step == "labour_rate":
        if skip:
            return _start_overhead(st)
        if _YES_RE.search(text) and st["data"].get("labour_suggest"):
            st["data"]["labour_rate_cents"] = st["data"]["labour_suggest"]
        else:
            r = parse_rate(text)
            if r is None:
                return "Send the rate per hour, e.g. \"R95 an hour\" or \"R400 a day\" — or *0*."
            st["data"]["labour_rate_cents"] = r
        return _start_overhead(st)

    # 4. overhead
    if step == "overhead_total":
        if skip:
            st["data"]["overhead_rule"] = "none"
            return _settings_preview(st)
        c = parse_money(text)
        if c is None:
            return "Send the monthly total, e.g. \"R12,000\" — or *none*."
        st["data"]["overhead_monthly_cents"] = c
        st["step"] = "overhead_rule"
        return _ask_rule()

    if step == "overhead_rule":
        rule = _rule_from(text)
        if skip or rule == "none":
            st["data"]["overhead_rule"] = "none"
            return _settings_preview(st)
        if not rule:
            return _ask_rule()
        st["data"]["overhead_rule"] = rule
        if rule == "per_labour_hour" and st["data"].get("hours_week"):
            return _overhead_from_count(st, hist.hours_per_month(st["data"]["hours_week"]))
        st["step"] = "overhead_count"
        return {"per_labour_hour": "How many hours a week does your team spend making things, all together?",
                "per_run": "About how many batches do you make in a month?",
                "per_unit": "About how many units do you make in a month?"}[rule]

    if step == "overhead_count":
        rule = st["data"]["overhead_rule"]
        n = parse_hours_week(text) if rule == "per_labour_hour" else _num(text)
        if not n or n <= 0:
            return "Send a number, please."
        if rule == "per_labour_hour":
            st["data"]["hours_week"] = n
            n = hist.hours_per_month(n)
        return _overhead_from_count(st, n)

    if step == "overhead_rate":
        if _YES_RE.search(text):
            return _settings_preview(st)
        c = parse_money(text)
        if c is None:
            return "Reply *yes*, or send the amount you'd rather use."
        st["data"]["overhead_rate_cents"] = c
        return _settings_preview(st)

    if step == "settings_confirm":
        if _YES_RE.search(text):
            d = st["data"]
            res = mfg.update_settings(tenant_id, labour_rate_cents=d.get("labour_rate_cents"),
                                      overhead_rule=d.get("overhead_rule") or "none",
                                      overhead_rate_cents=d.get("overhead_rate_cents") or 0,
                                      actor=phone, confirm=True)
            return _finish(tenant_id, st, res.get("reply_verbatim") or "✅ Saved.")
        if _NO_RE.search(text) or skip:
            return _finish(tenant_id, st, "Labour and overhead left as they were.")
        return "Reply *yes* to save, or *no* to leave them."
    st["step"] = "end"
    return "Send *set up costing* to start again."


async def _recipe_preview(tenant_id: str, st: Dict[str, Any]) -> str:
    from vula.commerce import manufacturing as mfg
    r = st["recipe"]
    res = await mfg.set_recipe(tenant_id, "", r["ingredients"], makes=r["makes"], labour_hours=r["hours"],
                               product_id=st["product"]["id"], confirm=False)
    if res.get("error"):
        st["step"] = "recipe"
        return f"{res['error']}\n\n" + _ask_recipe(st["product"]["name"])
    st["step"] = "recipe_confirm"
    lines = "\n".join(f"• {costing._n(i['qty'])} {i['unit']} {i['material']}" for i in r["ingredients"])
    new = f"\n(New materials, no price yet: {res['new_materials'].split(' — ')[0]})" if res.get("new_materials") else ""
    return (f"*{st['product']['name']}* — one batch:\n{lines}\nMakes {costing._n(r['makes'])} good; "
            f"{costing._n(r['hours'])} labour hours.{new}\n\nSave this recipe? *yes* / *no*")


def _start_prices(tenant_id: str, st: Dict[str, Any]) -> str:
    ids = set()
    for p in st["products"]:
        bom = costing.active_bom(tenant_id, product_id=p["id"])
        ids |= {str(ln["material_id"]) for ln in (bom or {}).get("lines", [])}
    mats = costing.list_materials(tenant_id)
    unpriced = [m for m in mats if m.get("unit_cost") is None and (not ids or str(m["id"]) in ids)]
    st["queue"] = unpriced
    try:
        st["rows"] = hist.material_rows(tenant_id) if unpriced else []
    except Exception:
        st["rows"] = []
    if not unpriced:
        return _start_labour(tenant_id, st, "Every material has a price ✅")
    return _ask_price(tenant_id, st, f"Now prices — {len(unpriced)} material{'s' if len(unpriced) != 1 else ''} "
                                     "without one.")


def _ask_price(tenant_id: str, st: Dict[str, Any], lead: str) -> str:
    mat = st["queue"][0]
    st["step"] = "price"
    sug = hist.price_suggestions(tenant_id, mat["name"], material=mat, rows=st.get("rows"))
    st["sug"] = sug
    if not sug:
        return (f"{lead}\n\nWhat do you pay for *{mat['name']}*? e.g. \"R95 for a 25 kg bag\" or "
                f"\"R3.80 per kg\" (excluding VAT if you claim it back).")
    lines = "\n".join(hist.suggestion_line(s, i + 1) for i, s in enumerate(sug))
    return (f"{lead}\n\n*{mat['name']}* — your invoices show:\n{lines}\n\nReply *yes* for 1, the number of "
            "another, or what you pay (e.g. \"R95 for a 25 kg bag\").")


def _next_price(tenant_id: str, st: Dict[str, Any], done: str) -> str:
    st["queue"] = st["queue"][1:]
    if st["queue"]:
        return _ask_price(tenant_id, st, done)
    return _start_labour(tenant_id, st, done)


def _start_labour(tenant_id: str, st: Dict[str, Any], lead: str) -> str:
    try:
        st["bank"] = hist.bank_suggestions(tenant_id)
    except Exception as exc:
        log.debug("bank suggestions failed: %s", exc)
        st["bank"] = {"labour_monthly_cents": None, "overhead_monthly_cents": None, "overhead_by_account": []}
    if st["bank"].get("labour_monthly_cents"):
        st["step"] = "labour_hours"
        return (f"{lead}\n\nLabour: from your bank, wages and casual labour came to about "
                f"*{rands(st['bank']['labour_monthly_cents'])} a month* (since {st['bank']['since']}). How many "
                "hours a week does your production team work, all together? e.g. \"2 people, 40 hours each\".")
    return _ask_labour_rate(st, lead)


def _ask_labour_rate(st: Dict[str, Any], lead: str) -> str:
    st["step"] = "labour_rate"
    st["data"].pop("labour_suggest", None)
    return ((lead + "\n\n" if lead else "") + "Labour: what does an hour of work cost you? e.g. \"R95 an hour\" "
            "or \"R400 a day\" (a day = 8 hours) — *0* if you don't count labour.")


def _start_overhead(st: Dict[str, Any]) -> str:
    b = st.get("bank") or {}
    rate = st["data"].get("labour_rate_cents")
    lead = f"Labour: {rands(rate)} an hour ✅" if rate is not None else ""
    if b.get("overhead_monthly_cents"):
        st["data"]["overhead_monthly_cents"] = b["overhead_monthly_cents"]
        parts = " · ".join(f"{label} {rands(c)}" for label, c in b["overhead_by_account"][:6])
        st["step"] = "overhead_rule"
        return (f"{lead}\n\nRunning costs from your bank: {parts} — about "
                f"*{rands(b['overhead_monthly_cents'])} a month*.\n" + _ask_rule())
    st["step"] = "overhead_total"
    return (f"{lead}\n\nRunning costs: what do rent, electricity, insurance and the like come to a month, "
            "all together? e.g. \"R12,000\" — or *none*.")


def _ask_rule() -> str:
    return ("How should I spread them over what you make?\n1. per labour hour\n2. per batch\n"
            "3. per unit made\n4. don't add running costs")


def _rule_from(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if t in ("1",) or "hour" in t:
        return "per_labour_hour"
    if t in ("2",) or "batch" in t or re.search(r"\bruns?\b", t):
        return "per_run"
    if t in ("3",) or "unit" in t or "each" in t or "item" in t:
        return "per_unit"
    if t in ("4", "0") or "don" in t or "none" in t or "no " in t + " ":
        return "none"
    return None


def _overhead_from_count(st: Dict[str, Any], count: Decimal) -> str:
    rule = st["data"]["overhead_rule"]
    rate = hist.per(st["data"]["overhead_monthly_cents"], count)
    st["data"]["overhead_rate_cents"] = rate
    st["step"] = "overhead_rate"
    what = {"per_labour_hour": "labour hour", "per_run": "batch", "per_unit": "unit"}[rule]
    return (f"That's {rands(rate)} per {what} ({rands(st['data']['overhead_monthly_cents'])} a month ÷ "
            f"{costing._n(count)}). Use it? *yes* — or send the amount you'd rather use.")


def _settings_preview(st: Dict[str, Any]) -> str:
    d = st["data"]
    st["step"] = "settings_confirm"
    rule = d.get("overhead_rule") or "none"
    over = ("no running costs added" if rule == "none" else
            f"{rands(d.get('overhead_rate_cents') or 0)} per "
            + {"per_labour_hour": "labour hour", "per_run": "batch", "per_unit": "unit made"}[rule])
    lab = rands(d["labour_rate_cents"]) + " an hour" if d.get("labour_rate_cents") is not None else "unchanged"
    return f"Save this? Labour {lab}; running costs: {over}. *yes* / *no*"


def _finish(tenant_id: str, st: Dict[str, Any], lead: str) -> str:
    st["step"] = "end"
    sheets = []
    for p in st["products"][:5]:
        try:
            sheets.append(costing.cost_sheet_text(costing.cost_sheet(tenant_id, _product_row(tenant_id, p))))
        except Exception as exc:
            log.debug("cost sheet for %s failed: %s", p.get("name"), exc)
    body = ("\n\n".join(sheets) if sheets else
            "Send *set up costing* again to add a product's recipe, or ask \"what does a … cost us?\".")
    return (f"{lead}\n\nCosting is set up ✅\n\n{body}\n\nChange anything later in Sell › Costing, or just tell me "
            "(e.g. \"labour is R100 an hour\", \"made 12 basins, 1 reject, 6 hours\").")


def _product_row(tenant_id: str, p: Dict[str, Any]) -> Dict[str, Any]:
    rows = (costing._db().table("commerce_products").select("*").eq("tenant_id", tenant_id)
            .eq("id", p["id"]).limit(1).execute().data or [])
    return rows[0] if rows else p


def active(tenant_id: str, phone: str) -> bool:
    st = _ACTIVE.get((tenant_id, phone))
    return bool(st) and time.time() - st["t"] <= _TTL


def _reset() -> None:            # tests
    _ACTIVE.clear()
