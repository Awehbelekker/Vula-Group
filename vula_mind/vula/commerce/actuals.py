"""
vula/commerce/actuals.py — what the job actually cost against its estimate, and what Vula learns.

Trade costing, phase 4 (15 Nov 2026). Ian: "Vula learns". An estimate tied to a project
(vula_estimates.project) is compared with what was PAID on that project — the lines of its filed
invoices, expenses and labour payments (vula_price_observations, kept up to date on filing and
when a document moves project). All in code, no model call.

  matching   each paid line goes to at most one component of the estimate: the one whose most
             specific search words (trade_checklists.search_terms) it matches. A line that matches
             two equally ("sand" with mortar sand and plaster sand in the estimate) is listed as
             "couldn't tell which" and not counted. A line matching nothing is listed as bought
             for the project but not in the estimate (other work, or delivery).
  units      same unit; day ↔ hour (8 h); a pack size named on the line ("CEMENT 50KG") when the
             build-up counts in kg/l/m. Anything else is listed, not guessed.
  groups     components that are the same thing bought (mortar cement for the walls and cement for
             the plaster — both "cement") are compared as one total: the purchase can't be split
             between items, so no build-up change is suggested from them.
  learning   only when the work is finished (the project is complete, or an owner or manager says
             so): a component attributable to one item, with a bought quantity more than 15% from
             the estimate, becomes a SUGGESTION — waste for a material (keeping the cited quantity),
             hours per unit for labour paid by the hour or day. A result that can't be right (waste
             over 40% or under -5%, labour off by more than 4×) is flagged "check this" instead. An
             owner or manager accepts (a new build-up version, read back) or dismisses it.

Prices are not learned here: the price book already learns them from every filed invoice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from vula.commerce import buildups, trade_checklists
from vula.commerce.estimate import HOURS_PER_DAY, rand

log = logging.getLogger(__name__)

TABLE = "vula_buildup_suggestions"
GAP_PCT = Decimal(15)                     # a bought quantity this far from the estimate is worth a look
WASTE_RANGE = (Decimal(-5), Decimal(40))  # outside this the invoices or the measurement are suspect
LABOUR_RATIO = (Decimal("0.25"), Decimal(4))
_PAID = ("invoice", "expense", "labour_payment")


class ActualsError(ValueError):
    """Said to the person as it is."""


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


def _db():
    from vula.commerce import service
    return service._client()


def _unit(u: Any) -> str:
    from vula.takeoff.market_checks import norm_unit
    return norm_unit(u)


def _show(v: Decimal) -> str:
    v = v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    return format(v, "f")


def _cents(v: Decimal) -> int:
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ── reading ──────────────────────────────────────────────────────────────────

def get_estimate(tenant_id: str, estimate_id: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table("vula_estimates").select("*").eq("tenant_id", tenant_id).eq("id", estimate_id)
            .limit(1).execute().data or [])
    return rows[0] if rows else None


def _project_names(tenant_id: str, project: str) -> List[str]:
    """The project's name and its aliases — documents filed before a rename still count."""
    from vula.commerce import service
    key = service.project_key(project)
    for r in service.registered_projects(tenant_id):
        if key in (service.project_key(r.get("name")), *(service.project_key(a) for a in r.get("aliases") or [])):
            return list(dict.fromkeys([r["name"], *(r.get("aliases") or [])]))
    return [project]


def paid_lines(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    """Every line paid on the project (this business only), newest first, without repeats."""
    from vula.commerce import price_book
    seen, out = set(), []
    for name in _project_names(tenant_id, project):
        for r in price_book._fetch(tenant_id, "", None, name):
            if r.get("source_kind") not in _PAID:
                continue
            key = (r.get("doc_id"), r.get("description"), r.get("quantity"), r.get("unit_price_cents"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


# ── matching ─────────────────────────────────────────────────────────────────

def _group_key(component: str, label: str, kind: str) -> str:
    """What is actually bought: components with the same search words are the same purchase."""
    return f"{kind}:{trade_checklists.search_terms(component, label)[0]}"


def _score(norm_key: str, terms: List[str]) -> int:
    """How specifically the line names the component: the most words of one of its search terms."""
    from vula.commerce.price_book import _matches
    return max((len(t.split()) for t in terms if _matches(norm_key or "", t)), default=0)


def _bought_qty(line: Dict[str, Any], unit: str) -> Tuple[Optional[Decimal], str]:
    """(quantity in the build-up's unit, how) — or (None, reason) when it can't be compared."""
    from vula.commerce.costing_history import pack_from_text
    if line.get("quantity") in (None, ""):
        return None, "no quantity on the invoice"
    q = _d(line["quantity"])
    f, t = _unit(line.get("unit")), _unit(unit)
    if f == t:
        return q, ""
    if f == "day" and t == "hour":
        return q * HOURS_PER_DAY, f"{_show(q)} day × {int(HOURS_PER_DAY)} h"
    if f == "hour" and t == "day":
        return q / HOURS_PER_DAY, f"{_show(q)} h ÷ {int(HOURS_PER_DAY)}"
    if t in ("kg", "l", "m") and f in ("", "each", "bag", "unit", "pack", "roll"):
        pack = pack_from_text(line.get("description") or "")
        if pack and _unit(pack[1]) == t:
            return q * pack[0], f"{_show(q)} × {_show(pack[0])} {t}"
    return None, f"bought per {line.get('unit') or 'item'}, the build-up counts per {unit}"


def _paid_cents(line: Dict[str, Any]) -> int:
    q = _d(line["quantity"]) if line.get("quantity") not in (None, "") else Decimal(1)
    return _cents(q * _d(line.get("unit_price_cents") or 0))


def compare(tenant_id: str, est: Dict[str, Any], lines: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The estimate beside what was paid on its project. See the module docstring."""
    project = est.get("project")
    if not project:
        raise ActualsError("This estimate isn't tied to a project, so there's nothing to compare it with. "
                           "Make the estimate again with the project chosen.")
    paid = paid_lines(tenant_id, project) if lines is None else lines
    groups: Dict[str, Dict[str, Any]] = {}
    for it in est.get("items") or []:
        if it.get("status") != "priced":
            continue
        q = _d(it["quantity"])
        for ln in it.get("lines") or []:
            kind = ln.get("kind") or "material"
            gk = _group_key(ln["component"], ln.get("label") or "", kind)
            g = groups.setdefault(gk, {
                "key": gk, "label": ln.get("label") or ln["component"], "kind": kind, "unit": ln["unit"],
                "terms": trade_checklists.search_terms(ln["component"], ln.get("label") or ""),
                "uses": [], "estimated_qty": Decimal(0), "estimated_cents": 0,
                "bought_qty": Decimal(0), "paid_cents": 0, "lines": [], "not_comparable": []})
            if _unit(ln["unit"]) != _unit(g["unit"]):
                g["mixed_units"] = True
            g["uses"].append({"item": it["item"], "spec": it.get("spec") or {}, "buildup_id": it.get("buildup_id"),
                              "buildup_origin": it.get("buildup_origin"), "component": ln["component"],
                              "item_qty": q, "per_unit": _d(ln["per_unit"]), "waste_pct": _d(ln.get("waste_pct") or 0)})
            g["estimated_qty"] += _d(ln["quantity"])
            g["estimated_cents"] += int(ln.get("cost_cents") or 0)
    unmatched, unclear = [], []
    for r in paid:
        kind = r.get("kind") or "material"
        cands = [(s, gk) for gk, g in groups.items()
                 if g["kind"] == kind and (s := _score(r.get("norm_key") or "", g["terms"]))]
        if not cands:
            unmatched.append(r)
            continue
        cands.sort(key=lambda c: -c[0])
        if len(cands) > 1 and cands[0][0] == cands[1][0]:
            unclear.append({**_evidence(r), "could_be": [groups[gk]["label"] for s, gk in cands if s == cands[0][0]]})
            continue
        g = groups[cands[0][1]]
        qty, how = _bought_qty(r, g["unit"])
        g["paid_cents"] += _paid_cents(r)
        ev = _evidence(r)
        if qty is None:
            g["not_comparable"].append({**ev, "why": how})
        else:
            g["bought_qty"] += qty
            g["lines"].append({**ev, "counted": _show(qty), "how": how})
    out_groups = []
    for g in groups.values():
        est_q, got = g["estimated_qty"], g["bought_qty"]
        gap = ((got - est_q) / est_q * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) \
            if est_q > 0 and g["lines"] else None
        shared = len({(u["item"], u["component"]) for u in g["uses"]}) > 1
        out_groups.append({
            "key": g["key"], "label": g["label"], "kind": g["kind"], "unit": g["unit"],
            "items": list(dict.fromkeys(u["item"] for u in g["uses"])),
            "estimated_qty": _show(est_q), "bought_qty": _show(got) if g["lines"] else None,
            "gap_pct": str(gap) if gap is not None else None,
            "estimated_cents": g["estimated_cents"], "paid_cents": g["paid_cents"],
            "shared": shared, "lines": g["lines"], "not_comparable": g["not_comparable"],
            "_uses": g["uses"], "_got": got, "_est": est_q,
        })
    return {"estimate_id": est.get("id"), "title": est.get("title"), "project": project,
            "groups": out_groups, "unclear": unclear,
            "not_in_estimate": [_evidence(r) for r in unmatched],
            "not_in_estimate_cents": sum(_paid_cents(r) for r in unmatched),
            "estimated_cents": int(est.get("total_cents") or 0),
            "paid_cents": sum(g["paid_cents"] for g in out_groups)}


def _evidence(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"doc_id": r.get("doc_id"), "supplier": r.get("supplier"), "date": r.get("observed_on"),
            "description": r.get("description"), "quantity": r.get("quantity"), "unit": r.get("unit"),
            "unit_price_cents": r.get("unit_price_cents"), "source": r.get("source_kind")}


# ── what it suggests ─────────────────────────────────────────────────────────

def proposals(cmp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For each comparable component: no change, a suggested change, or "check this"."""
    out = []
    for g in cmp["groups"]:
        if g["bought_qty"] is None or g["shared"] or g.get("mixed_units") or g["_est"] <= 0:
            continue
        gap = _d(g["gap_pct"])
        u = g["_uses"][0]
        base = {"group": g["key"], "label": g["label"], "kind": g["kind"], "unit": g["unit"],
                "item": u["item"], "spec": u["spec"], "buildup_id": u["buildup_id"], "component": u["component"],
                "estimated_qty": g["estimated_qty"], "bought_qty": g["bought_qty"], "gap_pct": g["gap_pct"],
                "evidence": g["lines"]}
        if abs(gap) <= GAP_PCT:
            out.append({**base, "verdict": "close"})
            continue
        if g["kind"] == "labour":
            ratio = g["_got"] / g["_est"]
            if not (LABOUR_RATIO[0] <= ratio <= LABOUR_RATIO[1]):
                out.append({**base, "verdict": "check"})
                continue
            per = (g["_got"] / (u["item_qty"] * (1 + u["waste_pct"] / 100))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            out.append({**base, "verdict": "suggest", "field": "qty_per_unit",
                        "current_value": u["per_unit"], "suggested_value": per})
            continue
        theory = u["item_qty"] * u["per_unit"]
        waste = ((g["_got"] / theory - 1) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if not (WASTE_RANGE[0] <= waste <= WASTE_RANGE[1]):
            out.append({**base, "verdict": "check", "implied_waste_pct": str(waste)})
            continue
        new = max(Decimal(0), waste)
        if new == u["waste_pct"]:
            out.append({**base, "verdict": "close"})
            continue
        out.append({**base, "verdict": "suggest", "field": "waste_pct",
                    "current_value": u["waste_pct"], "suggested_value": new})
    return out


async def learn(tenant_id: str, estimate_id: str, *, person: str = "", full_access: bool,
                notify: bool = True) -> Dict[str, Any]:
    """The work is finished: keep a suggestion for each component that came out far from the
    build-up (pending until an owner or manager decides). A suggestion already decided is left as
    it was; a pending one is brought up to date with the invoices filed since."""
    if not full_access:
        raise ActualsError("Only an owner or manager can say the work is finished — they'll be asked.")
    est = get_estimate(tenant_id, estimate_id)
    if not est:
        raise ActualsError("I can't find that estimate.")
    cmp = compare(tenant_id, est)
    props = proposals(cmp)
    db = _db()
    existing = {(r["buildup_id"], r["component"], r["field"]): r for r in
                (db.table(TABLE).select("*").eq("tenant_id", tenant_id).eq("estimate_id", estimate_id)
                 .execute().data or [])}
    new_ones = []
    for p in props:
        if p["verdict"] != "suggest" or not p.get("buildup_id"):
            continue
        row = {"tenant_id": tenant_id, "estimate_id": estimate_id, "buildup_id": p["buildup_id"],
               "project": cmp["project"], "item": p["item"], "spec": p["spec"], "component": p["component"],
               "label": p["label"], "kind": p["kind"], "field": p["field"],
               "current_value": str(p["current_value"]), "suggested_value": str(p["suggested_value"]),
               "estimated_qty": p["estimated_qty"], "bought_qty": p["bought_qty"], "unit": p["unit"],
               "evidence": p["evidence"][:40], "status": "pending"}
        old = existing.get((p["buildup_id"], p["component"], p["field"]))
        if old is None:
            got = db.table(TABLE).insert(row).execute().data or []
            if got:
                new_ones.append(got[0])
        elif old.get("status") == "pending":
            db.table(TABLE).update({k: v for k, v in row.items() if k != "status"}).eq("id", old["id"]).eq("tenant_id", tenant_id).execute()
    pending = list_suggestions(tenant_id, estimate_id=estimate_id)
    if notify and new_ones:
        await _tell(tenant_id, cmp["project"], new_ones)
    return {"comparison": public(cmp), "proposals": [_plain(p) for p in props], "suggestions": pending,
            "new": len(new_ones)}


async def learn_project(tenant_id: str, project: str) -> int:
    """A project was marked complete: learn from every estimate tied to it. Returns how many new
    suggestions there are. Best-effort — completing a project never fails because of this."""
    from vula import features
    if not features.on(tenant_id, "trade_costing"):
        return 0
    n = 0
    try:
        names = _project_names(tenant_id, project)
        rows = (_db().table("vula_estimates").select("id,project").eq("tenant_id", tenant_id)
                .in_("project", names).execute().data or [])
        for r in rows:
            try:
                res = await learn(tenant_id, r["id"], person="project complete", full_access=True)
                n += res["new"]
            except ActualsError as exc:
                log.debug("learn skipped for %s: %s", r["id"], exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("learning from project %s skipped: %s", project, exc)
    return n


async def _tell(tenant_id: str, project: str, rows: List[Dict[str, Any]]) -> None:
    lines = [f"• {describe_suggestion(r)}" for r in rows[:5]]
    text = (f"🧱 {project} is finished — compared with its estimate, Vula suggests:\n" + "\n".join(lines)
            + "\nOpen Estimating › Costing by trade to accept or dismiss each; nothing changes until you do.")
    try:
        from vula.integrations import notify
        await notify.notify_team(tenant_id, "buildup_suggestion", text,
                                 idem_key="buildup-suggestion:" + ",".join(sorted(str(r["id"]) for r in rows)))
    except Exception as exc:  # noqa: BLE001
        log.debug("suggestion notice skipped: %s", exc)


def is_finished(tenant_id: str, project: str) -> bool:
    from vula.commerce import service
    key = service.project_key(project)
    return any(r.get("status") == "complete" and key in (service.project_key(r.get("name")),
               *(service.project_key(a) for a in r.get("aliases") or []))
               for r in service.registered_projects(tenant_id))


# ── deciding ─────────────────────────────────────────────────────────────────

def list_suggestions(tenant_id: str, *, status: Optional[str] = "pending",
                     estimate_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = _db().table(TABLE).select("*").eq("tenant_id", tenant_id)
    if status:
        q = q.eq("status", status)
    if estimate_id:
        q = q.eq("estimate_id", estimate_id)
    rows = q.order("created_at", desc=True).execute().data or []
    return [{**r, "text": describe_suggestion(r)} for r in rows]


def _suggestion(tenant_id: str, sid: str) -> Dict[str, Any]:
    rows = _db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", sid).limit(1).execute().data or []
    if not rows:
        raise ActualsError("I can't find that suggestion.")
    if rows[0].get("status") != "pending":
        raise ActualsError(f"That suggestion was already {rows[0]['status']}.")
    return rows[0]


def accept(tenant_id: str, sid: str, *, person: str, full_access: bool) -> Dict[str, Any]:
    """Save the build-up with this one change (a new version, read back), then mark it accepted.
    If the build-up was changed since, the change goes to the one in use now."""
    if not full_access:
        raise ActualsError("Only an owner or manager can change a build-up.")
    s = _suggestion(tenant_id, sid)
    b = buildups.get(tenant_id, s["buildup_id"])
    if not b or b.get("status") == "retired":
        b = buildups.find(tenant_id, s["item"], s.get("spec") or {})
    if not b:
        raise ActualsError("That build-up isn't in use any more, so there's nothing to change.")
    value = _d(s["suggested_value"])
    try:
        new = buildups.confirm(tenant_id, b["id"], person=person, full_access=True,
                               changes=[{"component": s["component"], s["field"]: str(value)}])
    except buildups.BuildupError as exc:
        raise ActualsError(str(exc))
    line = next((ln for ln in new.get("lines") or [] if ln["component"] == s["component"]), None)
    if line is None or _d(line[s["field"]]) != value:
        raise ActualsError("The build-up didn't save the change — nothing was changed. Please try again.")
    _db().table(TABLE).update({"status": "accepted", "decided_by": person or None,
                               "decided_at": datetime.now(timezone.utc).isoformat(),
                               "new_buildup_id": new["id"]}).eq("id", sid).eq("tenant_id", tenant_id).execute()
    return new


def dismiss(tenant_id: str, sid: str, *, person: str, full_access: bool) -> bool:
    if not full_access:
        raise ActualsError("Only an owner or manager can dismiss a suggestion.")
    _suggestion(tenant_id, sid)
    _db().table(TABLE).update({"status": "dismissed", "decided_by": person or None,
                               "decided_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("id", sid).eq("tenant_id", tenant_id).execute()
    rows = _db().table(TABLE).select("status").eq("tenant_id", tenant_id).eq("id", sid).execute().data or []
    return bool(rows and rows[0].get("status") == "dismissed")


# ── wording ──────────────────────────────────────────────────────────────────

def change_text(s: Dict[str, Any]) -> str:
    cur, new = _show(_d(s["current_value"])), _show(_d(s["suggested_value"]))
    if s["field"] == "waste_pct":
        return f"waste {cur}% → {new}%"
    return f"{cur} → {new} {s.get('unit') or ''} per unit".replace("  ", " ")


def describe_suggestion(s: Dict[str, Any]) -> str:
    return (f"{s['item']} — {s.get('label') or s['component']}: {change_text(s)} "
            f"(estimated {s.get('estimated_qty')} {s.get('unit') or ''}, bought {s.get('bought_qty')})")


def _plain(p: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in p.items() if k != "evidence"}
    for k in ("current_value", "suggested_value"):
        if isinstance(out.get(k), Decimal):
            out[k] = _show(out[k])
    return out


def public(cmp: Dict[str, Any]) -> Dict[str, Any]:
    return {**cmp, "groups": [{k: v for k, v in g.items() if not k.startswith("_")} for g in cmp["groups"]]}


def summary(cmp: Dict[str, Any], props: List[Dict[str, Any]], *, finished: bool) -> str:
    """A plain reply built in code: every figure from the comparison."""
    out = [f"{cmp['title']} — project {cmp['project']}: estimated {rand(cmp['estimated_cents'])}, "
           f"paid so far on what it covers {rand(cmp['paid_cents'])}."]
    by = {p["group"]: p for p in props}
    for g in cmp["groups"]:
        if g["bought_qty"] is None:
            out.append(f"• {g['label']}: estimated {g['estimated_qty']} {g['unit']}, nothing bought on file yet")
            continue
        line = (f"• {g['label']}: estimated {g['estimated_qty']} {g['unit']}, bought {g['bought_qty']} "
                f"({'+' if not g['gap_pct'].startswith('-') else ''}{g['gap_pct']}%)")
        p = by.get(g["key"])
        if g["shared"]:
            line += f" — used by {', '.join(g['items'])}, so compared as a total only"
        elif p and p["verdict"] == "suggest" and finished:
            line += f" — suggest {change_text(p)} for {p['item']}"
        elif p and p["verdict"] == "check":
            line += " — too far off to learn from: check which invoices were counted"
        out.append(line)
        if g["not_comparable"]:
            out.append(f"  Not counted ({len(g['not_comparable'])}): {g['not_comparable'][0]['why']}")
    if cmp["unclear"]:
        out.append("Couldn't tell which component: " + "; ".join(
            f"{u['description']} ({' or '.join(u['could_be'])})" for u in cmp["unclear"][:5]))
    if cmp["not_in_estimate"]:
        out.append(f"Also paid on this project but not in the estimate (other work?): "
                   f"{len(cmp['not_in_estimate'])} lines, {rand(cmp['not_in_estimate_cents'])}.")
    if not finished:
        out.append("The work isn't marked finished, so this is progress only — nothing is suggested yet.")
    return "\n".join(out)
