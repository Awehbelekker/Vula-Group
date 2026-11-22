"""
vula/commerce/estimate.py — what a piece of building work will cost, built up from its trade.

Trade costing, phase 3 (14 Nov 2026). Judy: "how much to build a 6 × 4 m wall, plastered, not
painted?" — 24 m² of one-brick walling and 48 m² of plaster (both sides):

  quantities  item quantity × each build-up line × (1 + its waste %), in Decimal. The item
              quantity comes as a number or an expression ("6*4*2") evaluated by safe_eval — the
              model never does the sum.
  prices      per component, best source first:
                1. what the business PAID (invoices, expenses, labour payments — price_book)
                2. what it QUOTED or put in a BOQ (price_book)
                3. a checked market price (vula/takeoff/market.py — a lookup; the web only when
                   it is missing or stale)
              An own price older than 30 days (materials) / 90 (labour) is still the business's
              own and is used, but the current market price is shown beside it. A component with
              no price anywhere is listed as not priced — never left out, never guessed.
  coverage    every checklist component of the trade that isn't in the build-up, and every one
              the build-up's research couldn't find a figure for, is listed — so nothing is
              silently missed. Exclusions the person named ("not painted") are listed as such.
  compare     the business's own all-in rate for the item (its rate library or BOQs), × the
              quantity, beside the built-up cost.

Money is integer cents, rounded once per line. Nothing here calls a model except market
refreshes (vula/takeoff/market.py) and build-up research when asked for.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from vula.commerce import buildups, trade_checklists

log = logging.getLogger(__name__)

STALE_DAYS = {"material": 30, "plant": 30, "labour": 90}
# 17 Nov 2026 audit: one request can't turn into hundreds of paid searches and model calls
MAX_ITEMS = 40               # items in one estimate
MAX_NEW_BUILDUPS = 3         # build-ups researched from the web in one estimate
MAX_PRICE_SEARCHES = 12      # market prices looked up on the web in one estimate
HOURS_PER_DAY = Decimal(8)            # labour priced per day, needed per hour (stated in the estimate)
TABLE = "vula_estimates"


class EstimateError(ValueError):
    """Said to the person as it is."""


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


def _cents(v: Decimal) -> int:
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rand(cents: int) -> str:
    neg = cents < 0
    whole, part = divmod(abs(int(cents)), 100)
    return f"{'-' if neg else ''}R{whole:,}.{part:02d}".replace(",", " ")


def _age_days(when: Any) -> Optional[int]:
    if not when:
        return None
    try:
        d = datetime.fromisoformat(str(when).replace("Z", "+00:00")).date() if "T" in str(when) \
            else date.fromisoformat(str(when)[:10])
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - d).days


def quantity_of(item: Dict[str, Any]) -> Decimal:
    """The item's quantity: a number, or an expression of the person's own figures ("6*4*2")."""
    raw = item.get("quantity")
    if raw in (None, ""):
        raw = item.get("quantity_expr")
    if raw in (None, ""):
        raise EstimateError(f"How much {item.get('item') or 'of that'}? Give the size, e.g. 6 x 4 m.")
    if isinstance(raw, (int, float)):
        q = _d(raw)
    else:
        from core.skills.calculations import safe_eval
        try:
            q = _d(safe_eval(str(raw).replace("×", "*").replace("x", "*").replace("X", "*")))
        except Exception:  # noqa: BLE001
            raise EstimateError(f"I couldn't work out the quantity from \"{raw}\".")
    if q <= 0:
        raise EstimateError("The quantity must be more than 0.")
    return q.quantize(Decimal("0.001"))


# ── prices ───────────────────────────────────────────────────────────────────

def _unit(u: Any) -> str:
    from vula.takeoff.market_checks import norm_unit
    return norm_unit(u)


def _convert(cents: int, from_unit: str, to_unit: str) -> Optional[Decimal]:
    """A price per from_unit as a price per to_unit, when that's a plain conversion; else None."""
    f, t = _unit(from_unit), _unit(to_unit)
    if f == t:
        return _d(cents)
    if f == "day" and t == "hour":
        return _d(cents) / HOURS_PER_DAY
    if f == "hour" and t == "day":
        return _d(cents) * HOURS_PER_DAY
    return None


def vat_registered(tenant_id: str) -> bool:
    """Whether the business is VAT registered (commerce_invoice_settings) — an estimate's prices
    are then its cost excl VAT (it claims supplier VAT back), otherwise incl."""
    from vula.commerce.costing import _vat
    try:
        return bool(_vat(tenant_id)[0])
    except Exception:  # noqa: BLE001
        return False


def _market_on_basis(cents: Any, registered: bool) -> int:
    """A market price on the estimate's VAT basis. SA retail prices are shown incl VAT (VAT Act
    s65 requires an advertised price to include it), so it is taken off for a registered business."""
    from vula.commerce.costing_history import cost_basis_price
    return cost_basis_price(_cents(_d(cents)), "incl", registered)[0]


def own_price(tenant_id: str, component: str, label: str, unit: str, kind: str,
              registered: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """The business's own price for the component: paid first, then quoted. With `registered`
    (VAT registered or not), every price is first put on that cost basis (migration 284)."""
    from core.skills.calculations import where_from
    from vula.commerce import price_book
    best = None
    for term in trade_checklists.search_terms(component, label):
        try:
            rows = price_book.rates(tenant_id, term, kind=kind if kind in ("material", "labour", "plant") else None,
                                    vat_registered=registered)
        except Exception as exc:  # noqa: BLE001
            log.debug("own price lookup skipped: %s", exc)
            rows = []
        for r in rows:
            per = _convert(int(r.get("rate_cents") or 0), r.get("unit") or "", unit)
            if per is None or per <= 0:
                continue
            cand = {"cents_per_unit": per, "basis": r.get("basis") or "quoted", "label": r.get("description"),
                    "date": r.get("latest_on"), "where": where_from({**r, "learned": True}),
                    "converted": _unit(r.get("unit")) != _unit(unit),
                    "vat_unknown": bool(r.get("vat_unknown"))}
            rank = (0 if cand["basis"] == "paid" else 1)
            if best is None or rank < best[0]:
                best = (rank, cand)
        if best and best[0] == 0:
            break
    return best[1] if best else None


async def market_price(component: str, label: str, unit: str, kind: str,
                       refresh: bool = True, registered: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    from vula.takeoff import market
    for term in trade_checklists.search_terms(component, label):
        try:
            found = await market.price_for(term, unit, kind=kind, refresh=refresh)
        except Exception as exc:  # noqa: BLE001
            log.debug("market price skipped: %s", exc)
            found = None
        if found and found.get("price_cents"):
            cents = found["price_cents"] if registered is None else _market_on_basis(found["price_cents"], registered)
            return {"cents_per_unit": _d(cents), "basis": "market", "label": found.get("label"),
                    "date": str(found.get("scraped_at") or "")[:10] or None, "where": found.get("where"),
                    "url": found.get("url"), "stale": bool(found.get("stale")),
                    "refreshed": bool(found.get("refreshed"))}
    return None


# ── the estimate ─────────────────────────────────────────────────────────────

async def estimate(tenant_id: str, items: List[Dict[str, Any]], *, exclusions: Optional[List[str]] = None,
                   research: bool = False, refresh: bool = True, created_by: str = "",
                   title: str = "", save: bool = True, project: Optional[str] = None) -> Dict[str, Any]:
    """Cost each item from its build-up. See the module docstring for the rules. `project` (a
    registered project) ties the estimate to it, so it can later be compared with what was paid."""
    if not items:
        raise EstimateError("What should I cost? e.g. \"a 6 x 4 m one-brick wall, plastered both sides\".")
    if len(items) > MAX_ITEMS:
        raise EstimateError(f"That's {len(items)} items — I can cost up to {MAX_ITEMS} at a time. "
                            "Split it into parts (e.g. one per trade).")
    from vula.takeoff import market
    token = market.limit_web(MAX_PRICE_SEARCHES if refresh else 0)
    try:
        return await _estimate(tenant_id, items, exclusions=exclusions, research=research, refresh=refresh,
                               created_by=created_by, title=title, save=save, project=project)
    finally:
        market.reset_web(token)


async def _estimate(tenant_id: str, items: List[Dict[str, Any]], *, exclusions: Optional[List[str]],
                    research: bool, refresh: bool, created_by: str, title: str, save: bool,
                    project: Optional[str]) -> Dict[str, Any]:
    project = registered_project(tenant_id, project) if project else None
    researched = 0
    out_items: List[Dict[str, Any]] = []
    totals = {"material": 0, "labour": 0, "plant": 0}
    unpriced: List[str] = []
    counts = {"paid": 0, "quoted": 0, "market": 0, "refreshed": 0}
    # one VAT basis for every price (16 Nov 2026): excl when the business is VAT registered (its
    # cost — it claims supplier VAT back), incl when it isn't
    registered = vat_registered(tenant_id)
    vat_unknown: List[str] = []
    for it in items:
        name, spec = str(it.get("item") or "").strip(), it.get("spec") or {}
        q = quantity_of(it)
        unit = str(it.get("unit") or "m2")
        b = buildups.find(tenant_id, name, spec)
        if b is None and research and researched >= MAX_NEW_BUILDUPS:
            out_items.append({"item": name, "spec": spec, "quantity": str(q), "unit": unit,
                              "status": "no_buildup",
                              "message": f"Not researched: I research up to {MAX_NEW_BUILDUPS} new build-ups "
                                         "per estimate. Ask again for this one."})
            continue
        if b is None and research:
            researched += 1
            try:
                b = await buildups.research(tenant_id, name, spec, unit=unit, created_by=created_by)
            except buildups.BuildupError as exc:
                out_items.append({"item": name, "spec": spec, "quantity": str(q), "unit": unit,
                                  "status": "no_buildup", "message": str(exc)})
                continue
        if b is None:
            out_items.append({"item": name, "spec": spec, "quantity": str(q), "unit": unit,
                              "status": "no_buildup",
                              "message": "No build-up on file for this yet — I can research one for an "
                                         "owner or manager to confirm."})
            continue
        lines_out, item_total = [], 0
        for ln in b["lines"]:
            per = _d(ln["qty_per_unit"])
            waste = _d(ln.get("waste_pct") or 0)
            qty = (q * per * (1 + waste / 100)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            kind = ln.get("kind") or "material"
            own = own_price(tenant_id, ln["component"], ln["label"], ln["unit"], kind, registered)
            price = own
            compare = None
            if own is not None:
                age = _age_days(own.get("date"))
                if age is None or age > STALE_DAYS.get(kind, 30):
                    compare = await market_price(ln["component"], ln["label"], ln["unit"], kind, refresh=refresh,
                                                 registered=registered)
            else:
                price = await market_price(ln["component"], ln["label"], ln["unit"], kind, refresh=refresh,
                                           registered=registered)
            row = {"component": ln["component"], "label": ln["label"], "kind": kind,
                   "per_unit": str(per.normalize()), "waste_pct": str(waste.normalize()),
                   "quantity": str(qty.normalize()), "unit": ln["unit"]}
            if price is None:
                row.update(priced=False)
                unpriced.append(ln["label"])
            else:
                # the line is priced at the unit price in whole cents, so the spreadsheet's own
                # quantity × unit price (estimate_sheet.py) gives exactly the same cost
                unit_cents = _cents(price["cents_per_unit"])
                cost = _cents(qty * unit_cents)
                row.update(priced=True, unit_price_cents=unit_cents, cost_cents=cost,
                           source=price["basis"], where=price.get("where"), date=price.get("date"),
                           price_label=price.get("label"), url=price.get("url"),
                           stale=bool(price.get("stale")), converted=bool(price.get("converted")),
                           vat_unknown=bool(price.get("vat_unknown")))
                if price.get("vat_unknown"):
                    vat_unknown.append(ln["label"])
                totals[kind] = totals.get(kind, 0) + cost
                item_total += cost
                counts[price["basis"]] = counts.get(price["basis"], 0) + 1
                if price.get("refreshed"):
                    counts["refreshed"] += 1
            if compare is not None:
                row["market_now_cents"] = _cents(compare["cents_per_unit"])   # same VAT basis
                row["market_now_where"] = compare.get("where")
                if compare.get("refreshed"):
                    counts["refreshed"] += 1
            lines_out.append(row)
        checklist = trade_checklists.checklist_for(b["trade"])
        present = {ln["component"] for ln in b["lines"]}
        unknown = {u.get("key") for u in (b.get("unknown") or [])}
        out_items.append({
            "item": b["item"], "spec": b.get("spec") or {}, "trade": b["trade"], "quantity": str(q),
            "unit": unit, "status": "priced", "buildup_id": b["id"], "buildup_origin": b["origin"],
            "lines": lines_out, "total_cents": item_total,
            "needs_figure": [c["label"] for c in checklist if c["key"] in unknown and c["key"] not in present],
            "not_in_buildup": [c["label"] for c in checklist if c["key"] not in present and c["key"] not in unknown],
            "compare": _compare(tenant_id, b["item"], unit, q, item_total),
        })
    total = sum(totals.values())
    result = {
        "title": title or "; ".join(f"{i['item']} {i['quantity']} {i['unit']}" for i in out_items),
        "items": out_items, "totals_cents": totals, "total_cents": total,
        "unpriced": unpriced, "exclusions": [str(e) for e in (exclusions or [])],
        "sources": counts, "hours_per_day": int(HOURS_PER_DAY), "project": project,
        "vat": "excl" if registered else "incl", "vat_unknown": list(dict.fromkeys(vat_unknown)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if save:
        result["id"] = _save(tenant_id, result, created_by)
    return result


def registered_project(tenant_id: str, name: str) -> str:
    """The register's own name for a project (name, number or alias, any spelling), or an
    EstimateError naming the projects there are — an estimate is never tied to a made-up project."""
    from vula.commerce import service
    key = service.project_key(name)
    rows = service.registered_projects(tenant_id)
    for r in rows:
        if key and key in (service.project_key(r.get("name")), service.project_key(r.get("number")),
                           *(service.project_key(a) for a in r.get("aliases") or [])):
            return r["name"]
    names = ", ".join(sorted(r["name"] for r in rows if r.get("status") != "complete")[:12])
    raise EstimateError(f"I can't find a project called \"{name}\"."
                        + (f" Your projects: {names}." if names else " Add it under Projects first."))


def _compare(tenant_id: str, item: str, unit: str, q: Decimal, built_cents: int) -> Optional[Dict[str, Any]]:
    """The business's own all-in rate for the item (rate library first, then learned from its
    BOQs/quotes), × the quantity, beside the built-up cost."""
    try:
        from core.skills.calculations import where_from
        from vula.api.qs import search_rates
        rows = search_rates(tenant_id, item, limit=10)
    except Exception as exc:  # noqa: BLE001
        log.debug("all-in rate lookup skipped: %s", exc)
        return None
    for r in rows:
        if _unit(r.get("unit")) != _unit(unit) or not r.get("rate"):
            continue
        rate_cents = _cents(_d(r["rate"]) * 100)
        total = _cents(_d(rate_cents) * q)
        return {"rate_cents": rate_cents, "total_cents": total, "where": where_from(r),
                "label": r.get("description"), "built_up_cents": built_cents,
                "difference_cents": built_cents - total}
    return None


def _save(tenant_id: str, est: Dict[str, Any], created_by: str) -> Optional[str]:
    try:
        from vula.commerce import service
        row = {"tenant_id": tenant_id, "created_by": created_by or None, "title": est["title"][:200],
               "items": est["items"], "totals": {**est["totals_cents"], "unpriced": est["unpriced"],
                                                  "exclusions": est["exclusions"], "sources": est["sources"],
                                                  "vat": est.get("vat"), "vat_unknown": est.get("vat_unknown") or []},
               "total_cents": est["total_cents"]}
        if est.get("project"):
            row["project"] = est["project"]
        got = service._client().table(TABLE).insert(row).execute().data or []
        return got[0]["id"] if got else None
    except Exception as exc:  # noqa: BLE001 — an estimate that can't be saved is still shown
        log.warning("estimate not saved for %s: %s", tenant_id, exc)
        return None


# ── wording ──────────────────────────────────────────────────────────────────

_BASIS = {"paid": "what you paid", "quoted": "your quotes/BOQs", "market": "market price"}


def vat_text(est: Dict[str, Any]) -> str:
    """Which VAT basis the prices and totals are on, and any price that didn't say."""
    if est.get("vat") == "excl":
        line = "Prices and totals exclude VAT (you're VAT registered, so you claim supplier VAT back)."
    elif est.get("vat") == "incl":
        line = "Prices and totals include VAT (you're not VAT registered, so VAT is part of your cost)."
    else:
        return ""
    if est.get("vat_unknown"):
        line += (" Couldn't tell whether VAT was included on: " + ", ".join(est["vat_unknown"])
                 + " — used as on the invoice.")
    return line


def summary(est: Dict[str, Any]) -> str:
    """A short plain reply for WhatsApp or the chat; the full breakdown goes in the spreadsheet.
    Every figure here is from the estimate dict — built in code, never by a model."""
    out = [f"Estimate: {est['title']}" + (f" — project {est['project']}" if est.get("project") else ""), f"Total {rand(est['total_cents'])}{' ' + est['vat'] + ' VAT' if est.get('vat') else ''} — materials "
           f"{rand(est['totals_cents'].get('material', 0))}, labour {rand(est['totals_cents'].get('labour', 0))}"
           + (f", plant {rand(est['totals_cents']['plant'])}" if est['totals_cents'].get('plant') else "") + "."]
    for it in est["items"]:
        if it["status"] != "priced":
            out.append(f"• {it['item']}: {it['message']}")
            continue
        head = f"• {it['item']} ({it['quantity']} {it['unit']}): {rand(it['total_cents'])}"
        if it["buildup_origin"] == "proposed":
            head += " — build-up not yet confirmed by an owner or manager"
        elif it["buildup_origin"] == "shared":
            head += " — build-up confirmed by another Vula business"
        out.append(head)
        c = it.get("compare")
        if c:
            out.append(f"  Your own rate: {rand(c['rate_cents'])}/{it['unit']} ({c['where']}) → {rand(c['total_cents'])}")
        stale = [ln for ln in it["lines"] if ln.get("market_now_cents")]
        for ln in stale[:3]:
            out.append(f"  {ln['label']}: your price is from {ln.get('date')}; market now "
                       f"{rand(ln['market_now_cents'])}/{ln['unit']} ({ln.get('market_now_where')})")
        if it["needs_figure"]:
            out.append("  Needs a figure: " + ", ".join(it["needs_figure"]))
        if it["not_in_buildup"]:
            out.append("  Not in the build-up: " + ", ".join(it["not_in_buildup"]))
    if est["unpriced"]:
        out.append("Not priced (no price on file or checked online): " + ", ".join(dict.fromkeys(est["unpriced"])))
    if est["exclusions"]:
        out.append("Not included, as asked: " + ", ".join(est["exclusions"]))
    vat_line = vat_text(est)
    if vat_line:
        out.append(vat_line)
    s = est["sources"]
    used = ", ".join(f"{n} from {_BASIS[k]}" for k, n in s.items() if k in _BASIS and n)
    if used:
        out.append(f"Prices: {used}" + (f" ({s['refreshed']} refreshed online)" if s.get("refreshed") else "") + ".")
    return "\n".join(out)
