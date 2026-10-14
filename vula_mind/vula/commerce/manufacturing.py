"""
vula/commerce/manufacturing.py — the owner's side of costing: materials, recipes, batches.

The same flows serve WhatsApp (core/skills/commerce_admin.py's costing tools) and the dashboard
(vula/api/manufacturing.py), so a business sets itself up either way — no per-tenant build:

    "add material concrete mix, 25 kg bags"                 → add_material
    "got 2 bags concrete mix for R190"                      → receive (weighted-average cost)
    "recipe for round basin: 18 kg concrete mix, 150 g pigment, 0.25 l sealer, makes 1, 1.5 hours"
                                                             → set_recipe (new version; unknown
                                                               materials are added, unpriced)
    "made 12 round basins, 1 reject, 6 hours"                → log_batch
    "what does a round basin cost us?"                       → sheet

Every change is shown first and only made after the owner confirms (confirm=True), then read back
from the database before Vula says it's done. Arithmetic is vula/commerce/costing.py's.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from vula.commerce import costing
from vula.commerce.costing import CostingError, rands

log = logging.getLogger(__name__)


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").lower().split())


async def find_product(tenant_id: str, name: str = "", product_id: Optional[str] = None) -> Dict[str, Any]:
    """{product} or {candidates} (several match) or {} (none). Never a guess between several."""
    from vula.commerce import service
    products = await service.list_products(tenant_id, in_stock_only=False)
    if product_id:
        p = next((p for p in products if str(p.get("id")) == str(product_id)), None)
        return {"product": p} if p else {}
    want = _norm(name)
    if not want:
        return {}
    exact = [p for p in products if _norm(p.get("name")) == want]
    if exact:
        return {"product": exact[0]}
    singular = want[:-1] if want.endswith("s") else want          # "round basins" → "round basin"
    words = singular.split()
    hits = [p for p in products if all(w in _norm(p.get("name")) for w in words)]
    if len(hits) == 1:
        return {"product": hits[0]}
    return {"candidates": [p["name"] for p in hits[:8]]} if hits else {}


def _which(found: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    if found.get("candidates"):
        return {"status": "need_info", "message": "Which one? " + "; ".join(found["candidates"])}
    if not found.get("product"):
        return {"error": f"No product called '{name}'."}
    return None


# ── Materials ───────────────────────────────────────────────────────────────────────────

def add_material(tenant_id: str, name: str, unit: str, *, pack_size: Any = None, pack_unit: Optional[str] = None,
                 opening_qty: Any = None, opening_total_cents: Optional[int] = None,
                 supplier: Optional[str] = None, actor: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    base = costing.base_unit_for(unit)
    if costing.find_material(tenant_id, name) and \
            _norm(costing.find_material(tenant_id, name)["name"]) == _norm(name):
        return {"error": f"There's already a material called {name}."}
    pack = f", in {costing._n(pack_size)} {pack_unit} packs" if pack_size and pack_unit else ""
    opening = ""
    if opening_qty is not None:
        opening = f"; {costing._n(opening_qty)} {unit} on hand" + (
            f" costing {rands(int(opening_total_cents))}" if opening_total_cents is not None else "")
    if not confirm:
        return {"preview": True, "material": name, "measured_in": base,
                "message": f"Add {name} (measured in {base}{pack}{opening})? Call again with confirm=true."}
    m = costing.create_material(tenant_id, name, unit, pack_size=pack_size, pack_unit=pack_unit, supplier=supplier)
    if opening_qty is not None:
        costing.move_material(tenant_id, m, qty=opening_qty, unit=unit, total_cents=opening_total_cents,
                              reason="opening", ref_type="setup", actor=actor)
    back = costing.find_material(tenant_id, name)
    if not back:
        return {"error": f"{name} couldn't be saved — nothing was added.",
                "reply_verbatim": f"⚠️ {name} couldn't be saved — nothing was added."}
    text = f"✅ Added *{back['name']}* (measured in {back['base_unit']}{pack})."
    if back.get("unit_cost") is not None:
        text += f" Cost so far: {_per_unit_text(back)}."
    else:
        text += " No price yet." + _invoice_hint(tenant_id, back)
    return {"added": back["name"], "verified": True, "reply_verbatim": text}


def _invoice_hint(tenant_id: str, material: Dict[str, Any], rows: Optional[list] = None) -> str:
    """What past invoices suggest for an unpriced material, as a line to add to a reply."""
    from vula.commerce import costing_history as hist
    try:
        sug = hist.price_suggestions(tenant_id, material["name"], material=material, limit=1, rows=rows)
    except Exception as exc:
        log.debug("invoice price hint skipped: %s", exc)
        sug = []
    if not sug:
        return (f" Tell me what you pay, e.g. \"{material['name']} costs R95 for a 25 kg bag\", "
                "or book a delivery: \"got 2 bags for R190\".")
    return (f"\n💡 Your invoices show {hist.suggestion_line(sug[0])}. "
            f"Say *use the invoice price for {material['name']}* to set it.")


def price_material(tenant_id: str, material: str, *, pick: Optional[int] = None, total_cents: Optional[int] = None,
                   qty: Any = None, unit: Optional[str] = None, actor: Optional[str] = None,
                   confirm: bool = False) -> Dict[str, Any]:
    """Set what a material costs — from a past invoice (pick = which suggestion, 1 = best) or
    from what the owner says ("R95 for a 25 kg bag"). Without either, lists what the invoices
    show. Preview → confirm → read back, like every change."""
    from vula.commerce import costing_history as hist
    m = costing.find_material(tenant_id, material)
    if not m:
        return {"error": f"No material called '{material}' — add it first (e.g. \"add material {material}, measured in kg\")."}
    chosen, key, note, pack = None, None, None, None
    if total_cents is not None:
        if qty is None:
            qty, unit = 1, unit or m["base_unit"]
        u = (unit or m["base_unit"]).strip().lower()
        base_qty = costing.to_base(qty, u, m)
        if base_qty <= 0:
            return {"error": "How much does that price buy?"}
        chosen = (Decimal(int(total_cents)) / 100 / base_qty)
        note = f"owner: {rands(int(total_cents))} for {costing._n(qty)} {u}"
    else:
        sug = hist.price_suggestions(tenant_id, m["name"], material=m)
        if not sug:
            return {"status": "need_info",
                    "message": f"I found nothing like {m['name']} on your invoices. What do you pay for it "
                               f"(e.g. \"R95 for a 25 kg bag\")?"}
        if not pick:
            return {"status": "need_info", "suggestions": [hist.suggestion_line(x, i + 1) for i, x in enumerate(sug)],
                    "message": "Which invoice item is it (pick 1–%d), or what do you pay?" % len(sug)}
        if not 1 <= int(pick) <= len(sug):
            return {"error": f"Pick 1 to {len(sug)}."}
        s = sug[int(pick) - 1]
        chosen, key = s["unit_cost"], s["key"]
        note = f"invoice: {s['description']} {rands(s['invoice_price_cents'])} ({s.get('supplier') or '?'}, {s.get('latest_on') or '?'})"
        if s.get("pack_size") and not m.get("pack_size"):
            pack = (s["pack_size"], s["pack_unit"])
    shown = hist.show_unit_cost(chosen, m["base_unit"])
    if not confirm:
        out = {"preview": True, "material": m["name"], "price": shown, "from": note,
               "message": "Confirm to set this price (call again with confirm=true)."}
        if pack:
            out["pack"] = f"{costing._n(pack[0])} {pack[1]} (from the invoice wording)"
        return out
    back = hist.set_price(tenant_id, m, chosen, key=key, note=note, actor=actor)
    if pack:
        try:
            costing._db().table("commerce_materials").update(
                {"pack_size": float(pack[0]), "pack_unit": pack[1]}).eq("tenant_id", tenant_id) \
                .eq("id", m["id"]).execute()
        except Exception as exc:
            log.debug("pack size not saved: %s", exc)
    text = f"✅ {back['name']}: {_per_unit_text(back)}"
    if pack:
        text += f" (packs of {costing._n(pack[0])} {pack[1]})"
    return {"priced": back["name"], "verified": True, "reply_verbatim": text + "."}


def _per_unit_text(m: Dict[str, Any]) -> str:
    """R3.87 per kg / R0.10 per ml / R12.00 each — the average cost in a sensible unit."""
    c = Decimal(str(m["unit_cost"]))
    base = m.get("base_unit")
    if base == "g":
        return f"R{c * 1000:,.2f} per kg"
    if base == "ml":
        return f"R{c * 1000:,.2f} per l"
    return f"R{c:,.2f} " + ("each" if base == "each" else f"per {base}")


def receive(tenant_id: str, material: str, qty: Any, unit: str, total_cents: Optional[int], *,
            actor: Optional[str] = None, ref_type: str = "whatsapp", ref_id: Optional[str] = None,
            confirm: bool = False) -> Dict[str, Any]:
    m = costing.find_material(tenant_id, material)
    if not m:
        return {"error": f"No material called '{material}' — add it first (e.g. \"add material {material}, measured in kg\")."}
    base_qty = costing.to_base(qty, unit, m)
    if total_cents is None:
        return {"status": "need_info", "message": f"What did the {costing._n(qty)} {unit} of {m['name']} cost "
                                                  "(the total, excluding VAT you can claim back)?"}
    if not confirm:
        return {"preview": True, "material": m["name"], "receiving": costing.show_qty(base_qty, m["base_unit"]),
                "cost": rands(int(total_cents)),
                "message": "Confirm to book it in and update the average cost (call again with confirm=true)."}
    res = costing.move_material(tenant_id, m, qty=qty, unit=unit, total_cents=total_cents, reason="receive",
                                ref_type=ref_type, ref_id=ref_id, actor=actor)
    back = costing.find_material(tenant_id, m["name"]) or {}
    text = (f"✅ {m['name']}: +{costing.show_qty(base_qty, m['base_unit'])} booked in. "
            f"On hand {costing.show_qty(back.get('stock_qty', res['qty_after']), m['base_unit'])}; "
            f"average cost now {_per_unit_text(back) if back.get('unit_cost') is not None else 'unknown'}.")
    return {"received": m["name"], "verified": True, "reply_verbatim": text}


# ── Recipes ─────────────────────────────────────────────────────────────────────────────

async def set_recipe(tenant_id: str, product: str, ingredients: List[Dict[str, Any]], *, makes: Any = 1,
                     labour_hours: Any = 0, product_id: Optional[str] = None, actor: Optional[str] = None,
                     confirm: bool = False) -> Dict[str, Any]:
    """ingredients: [{material, qty, unit, waste_pct?}]. A material not on file yet is added on
    confirm (unpriced) — the cost sheet then says it has no price, it's never guessed."""
    found = await find_product(tenant_id, product, product_id)
    stop = _which(found, product)
    if stop:
        return stop
    prod = found["product"]
    if not ingredients:
        return {"status": "need_info", "message": f"What goes into one batch of {prod['name']}?"}
    lines, new = [], []
    for ing in ingredients:
        m = costing.find_material(tenant_id, ing.get("material") or "")
        if not m:
            try:
                base = costing.base_unit_for(ing.get("unit") or "each")
            except CostingError as exc:
                return {"error": str(exc)}
            m = {"id": None, "name": " ".join(str(ing.get("material")).split()), "base_unit": base}
            new.append(m["name"])
        try:
            costing.to_base(ing.get("qty"), ing.get("unit") or m["base_unit"], m)
        except CostingError as exc:
            return {"error": str(exc)}
        lines.append({"material": m, "qty": ing.get("qty"), "unit": ing.get("unit") or m["base_unit"],
                      "waste_pct": ing.get("waste_pct") or 0})
    current = costing.active_bom(tenant_id, product_id=prod["id"])
    if not confirm:
        out = {"preview": True, "product": prod["name"], "makes_per_batch": costing._n(makes),
               "labour_hours_per_batch": costing._n(labour_hours or 0),
               "ingredients": "; ".join(f"{costing._n(ln['qty'])} {ln['unit']} {ln['material']['name']}"
                                        + (f" (+{costing._n(ln['waste_pct'])}% waste)" if ln["waste_pct"] else "")
                                        for ln in lines),
               "message": ("Confirm to save this as the recipe"
                           + (f" (replaces version {current['version']}, which past batches keep)" if current else "")
                           + " — call again with confirm=true.")}
        if new:
            out["new_materials"] = ", ".join(new) + " — will be added with no price yet"
        return out
    for ln in lines:
        if ln["material"]["id"] is None:
            ln["material"] = costing.create_material(tenant_id, ln["material"]["name"], ln["unit"])
    bom_id = costing.save_recipe(tenant_id, product_id=prod["id"], lines=lines, batch_yield=makes,
                                 labour_hours=labour_hours, actor=actor)
    back = costing.active_bom(tenant_id, product_id=prod["id"])
    if not back or str(back["id"]) != bom_id or len(back["lines"]) != len(lines):
        return {"error": "The recipe couldn't be confirmed on read-back — nothing changed.",
                "reply_verbatim": "⚠️ The recipe couldn't be confirmed on read-back — please check Costing."}
    sheet = costing.cost_sheet(tenant_id, prod)
    text = f"✅ Recipe saved for *{prod['name']}* (version {back['version']}).\n\n" + costing.cost_sheet_text(sheet)
    unpriced = [ln["material"] for ln in lines if ln["material"].get("unit_cost") is None]
    if unpriced:
        from vula.commerce import costing_history as hist
        try:
            rows = hist.material_rows(tenant_id)
        except Exception:
            rows = []
        text += "\n" + "".join(_invoice_hint(tenant_id, mat, rows) for mat in unpriced[:4])
    return {"recipe": prod["name"], "version": back["version"], "verified": True, "reply_verbatim": text}


# ── Batches ─────────────────────────────────────────────────────────────────────────────

async def log_batch(tenant_id: str, product: str, *, good: Any, rejects: Any = 0, labour_hours: Any = None,
                    batches: Any = None, product_id: Optional[str] = None, actor: Optional[str] = None,
                    note: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    found = await find_product(tenant_id, product, product_id)
    stop = _which(found, product)
    if stop:
        return stop
    prod = found["product"]
    bom = costing.active_bom(tenant_id, product_id=prod["id"])
    if not bom:
        return {"error": f"{prod['name']} has no recipe yet — add one first, e.g. \"recipe for "
                         f"{prod['name']}: 18 kg concrete mix, makes 1\"."}
    g = costing.dec(good)
    r = costing.dec(rejects or 0)
    if batches is None:   # how many batches it took: the recipe's yield into everything made
        batches = (g + r) / costing.dec(bom["batch_yield"])
        if batches <= 0:
            batches = Decimal(1)
    mats = costing.materials_by_id(tenant_id)
    settings = costing.settings_for(tenant_id)
    now = costing.batch_cost(bom, bom["lines"], mats, settings, batches=batches, good=g, rejects=r,
                             labour_hours=labour_hours)
    planned = costing.batch_cost(bom, bom["lines"], mats, settings, batches=batches)
    if not now["complete"]:
        return {"error": "Can't cost this batch yet — no price for: " + ", ".join(now["missing_costs"])
                         + ". Book a delivery for each (e.g. \"got 2 bags for R190\") and log the batch again."}
    last = (costing.last_runs(tenant_id, product_id=prod["id"], limit=1) or [None])[0]
    driver = costing.main_driver(now, planned, last)
    if not confirm:
        return {"preview": True, "product": prod["name"], "good": costing._n(g), "rejects": costing._n(r),
                "labour_hours": costing._n(now["labour_hours"]),
                "materials": rands(now["materials_cents"]), "labour": rands(now["labour_cents"]),
                "overhead": rands(now["overhead_cents"]), "cost_per_unit": rands(now["unit_cost_cents"]),
                "message": "Confirm to log this batch: materials come off stock and the "
                           f"{costing._n(g)} good units go into stock (call again with confirm=true)."}
    run = costing.draft_run(tenant_id, bom, now, actor=actor, note=note)
    res = costing.confirm_run(tenant_id, str(run["id"]), actor=actor)
    if res.get("error"):
        return {"error": f"The batch couldn't be logged ({res['error']}).",
                "reply_verbatim": f"⚠️ The batch couldn't be logged ({res['error']})."}
    back = costing.get_run(tenant_id, str(run["id"])) or {}
    if back.get("status") != "confirmed" or back.get("total_cents") is None:
        return {"error": "The batch couldn't be confirmed on read-back.",
                "reply_verbatim": "⚠️ The batch couldn't be confirmed on read-back — please check Costing."}
    unit_c = costing.cents(costing.dec(back["unit_cost"]))
    text = (f"✅ Batch logged: {costing._n(g)} × {prod['name']}"
            + (f" ({costing._n(r)} rejected)" if r else "") + f", {costing._n(back['labour_hours'])} h.\n"
            f"Cost *{rands(unit_c)} each* — materials {rands(back['materials_cents'])}, labour "
            f"{rands(back['labour_cents'])}, overhead {rands(back['overhead_cents'])}.")
    if last and last.get("unit_cost") is not None:
        prev = costing.cents(costing.dec(last["unit_cost"]))
        diff = unit_c - prev
        text += f"\nLast batch {rands(prev)} ({'+' if diff >= 0 else '−'}{rands(abs(diff))})."
    if driver:
        text += f"\nMain driver: {driver}."
    if back.get("total_cents") != now["total_cents"]:
        text += "\n(A material price changed while this was being logged — the cost above is the one booked.)"
    return {"batch": str(run["id"]), "unit_cost": rands(unit_c), "verified": True, "reply_verbatim": text}


async def undo_last_batch(tenant_id: str, product: str, *, product_id: Optional[str] = None,
                          actor: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    found = await find_product(tenant_id, product, product_id)
    stop = _which(found, product)
    if stop:
        return stop
    prod = found["product"]
    last = (costing.last_runs(tenant_id, product_id=prod["id"], limit=1) or [None])[0]
    if not last:
        return {"error": f"There's no logged batch of {prod['name']} to undo."}
    if not confirm:
        return {"preview": True, "product": prod["name"], "batch_of": costing._n(last["good_qty"]),
                "logged": str(last.get("confirmed_at") or "")[:16].replace("T", " "),
                "message": "Confirm to reverse it: materials go back to stock and the units come off "
                           "(call again with confirm=true). Then log it again correctly."}
    res = costing.reverse_run(tenant_id, str(last["id"]), actor=actor)
    back = costing.get_run(tenant_id, str(last["id"])) or {}
    if res.get("error") or back.get("status") != "reversed":
        return {"error": "The batch couldn't be reversed.", "reply_verbatim": "⚠️ The batch couldn't be reversed."}
    return {"reversed": str(last["id"]), "verified": True,
            "reply_verbatim": f"↩️ Reversed the batch of {costing._n(last['good_qty'])} × {prod['name']}: "
                              "materials are back in stock and the units are off. Log it again when ready."}


# ── Settings and the cost sheet ─────────────────────────────────────────────────────────

def update_settings(tenant_id: str, *, labour_rate_cents: Optional[int] = None, overhead_rule: Optional[str] = None,
                    overhead_rate_cents: Optional[int] = None, actor: Optional[str] = None,
                    confirm: bool = False) -> Dict[str, Any]:
    if overhead_rule and overhead_rule not in costing.OVERHEAD_RULES:
        return {"error": f"The overhead rule must be one of: {', '.join(costing.OVERHEAD_RULES)}."}
    if not confirm:
        return {"preview": True, "labour_rate_per_hour": rands(labour_rate_cents) if labour_rate_cents is not None else "unchanged",
                "overhead_rule": overhead_rule or "unchanged",
                "overhead_rate": rands(overhead_rate_cents) if overhead_rate_cents is not None else "unchanged",
                "message": "Confirm to save (call again with confirm=true)."}
    s = costing.save_settings(tenant_id, labour_rate_cents=labour_rate_cents, overhead_rule=overhead_rule,
                              overhead_rate_cents=overhead_rate_cents, actor=actor)
    rule = {"none": "no overhead", "per_labour_hour": f"{rands(s['overhead_rate_cents'])} per labour hour",
            "per_run": f"{rands(s['overhead_rate_cents'])} per batch",
            "per_unit": f"{rands(s['overhead_rate_cents'])} per unit made"}[s["overhead_rule"]]
    return {"saved": True, "verified": True,
            "reply_verbatim": f"✅ Costing: labour {rands(s['labour_rate_cents'])} per hour; overhead {rule}."}


async def sheet(tenant_id: str, product: str, product_id: Optional[str] = None) -> Dict[str, Any]:
    found = await find_product(tenant_id, product, product_id)
    stop = _which(found, product)
    if stop:
        return stop
    s = costing.cost_sheet(tenant_id, found["product"])
    return {"product": found["product"]["name"], "reply_verbatim": costing.cost_sheet_text(s)}
