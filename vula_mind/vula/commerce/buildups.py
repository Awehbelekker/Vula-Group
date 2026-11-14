"""
vula/commerce/buildups.py — what goes into one unit of building work, researched once, confirmed
once, then looked up.

Trade costing, phase 2 (14 Nov 2026). Ian: "Vula needs to know the trades to be able to cost
properly", and "Vula learns, and later only looks up". A build-up is ONE unit of an item of work —
per m² of a 220 mm one-brick wall: so many bricks, so much mortar cement and sand, brickforce,
labour hours — each line with the waste allowed on it. Quantities only, never prices: prices come
from the business's own records and checked market prices when an estimate is made (phase 3).

  research()   one cited web search for the item, against its trade's checklist
               (vula/commerce/trade_checklists.py). A quantity is kept only when that number is
               printed on the page it cites; every checklist component without one is listed as
               unknown — nothing is guessed. Saved as `proposed`.
  confirm()    an owner or manager (full access) confirms it, optionally changing lines. A change,
               or confirming another business's shared build-up, makes this business its own new
               version; the one it replaces is retired. Read back after every write.
  find()       this business's confirmed build-up, else one another Vula business confirmed and
               shared (Ian, 14 Nov: "share quantities only"; never naming who), else its own
               proposed one. No web, no model.

All quantities are Decimal in code; the model never does arithmetic here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from vula.commerce import trade_checklists

log = logging.getLogger(__name__)

TABLE = "vula_buildups"
LINES = "vula_buildup_lines"
KINDS = ("material", "labour", "plant")
_PRIVATE = ("tenant_id", "created_by", "confirmed_by")


class BuildupError(ValueError):
    """Said to the person as it is."""


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── keys and wording ─────────────────────────────────────────────────────────

def _norm(text: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def clean_spec(spec: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """A spec as plain short strings: {"thickness": "220 mm", "plaster": "both sides"}."""
    out: Dict[str, str] = {}
    for k, v in (spec or {}).items():
        k, v = _norm(k)[:40], str(v or "").strip()[:80]
        if k and v:
            out[k] = v
    return out


def item_key(item: str, spec: Optional[Dict[str, Any]] = None) -> str:
    """The same item and spec, however they were worded, give the same key."""
    spec = clean_spec(spec)
    parts = [_norm(item)] + [f"{k}={_norm(v)}" for k, v in sorted(spec.items())]
    return "|".join(p for p in parts if p)


def spec_text(spec: Optional[Dict[str, Any]]) -> str:
    return ", ".join(clean_spec(spec).values())


def _num(v: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(v).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _show(d: Any) -> str:
    """104.0000 → "104", 0.0170 → "0.017"."""
    return format((_num(d) or Decimal(0)).normalize(), "f")


# ── reading ──────────────────────────────────────────────────────────────────

def _lines_of(buildup_id: str) -> List[Dict[str, Any]]:
    rows = (_db().table(LINES).select("*").eq("buildup_id", buildup_id)
            .order("position").execute().data or [])
    return sorted(rows, key=lambda r: int(r.get("position") or 0))


def _with_lines(row: Dict[str, Any], origin: str) -> Dict[str, Any]:
    out = dict(row)
    out["lines"] = _lines_of(row["id"])
    out["origin"] = origin
    if origin == "shared":                       # never say which business it came from
        for k in _PRIVATE:
            out.pop(k, None)
        for line in out["lines"]:
            line.pop("tenant_id", None)
    return out


def _latest(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rows = sorted(rows, key=lambda r: (int(r.get("version") or 0), str(r.get("confirmed_at") or r.get("created_at") or "")),
                  reverse=True)
    return rows[0] if rows else None


def find(tenant_id: str, item: str, spec: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The build-up to use for this item: own confirmed → shared confirmed → own proposed."""
    key = item_key(item, spec)
    rows = _db().table(TABLE).select("*").eq("item_key", key).execute().data or []
    own = [r for r in rows if r.get("tenant_id") == tenant_id]
    hit = _latest(r for r in own if r.get("status") == "confirmed")
    if hit:
        return _with_lines(hit, "own")
    hit = _latest(r for r in rows if r.get("tenant_id") != tenant_id and r.get("status") == "confirmed"
                  and r.get("shared"))
    if hit:
        return _with_lines(hit, "shared")
    hit = _latest(r for r in own if r.get("status") == "proposed")
    return _with_lines(hit, "proposed") if hit else None


def get(tenant_id: str, buildup_id: str) -> Optional[Dict[str, Any]]:
    """One build-up this business may see: its own (any status), or a shared confirmed one."""
    rows = _db().table(TABLE).select("*").eq("id", buildup_id).limit(1).execute().data or []
    if not rows:
        return None
    r = rows[0]
    if r.get("tenant_id") == tenant_id:
        return _with_lines(r, "own" if r.get("status") == "confirmed" else str(r.get("status")))
    if r.get("status") == "confirmed" and r.get("shared"):
        return _with_lines(r, "shared")
    return None


def list_for(tenant_id: str, include_retired: bool = False) -> List[Dict[str, Any]]:
    rows = _db().table(TABLE).select("*").eq("tenant_id", tenant_id).execute().data or []
    rows = [r for r in rows if include_retired or r.get("status") != "retired"]
    rows.sort(key=lambda r: (r.get("trade") or "", r.get("item") or "", -int(r.get("version") or 0)))
    return [_with_lines(r, "own" if r.get("status") == "confirmed" else str(r.get("status"))) for r in rows]


# ── writing ──────────────────────────────────────────────────────────────────

def _clean_lines(lines: Iterable[Dict[str, Any]], trade: Optional[str]) -> List[Dict[str, Any]]:
    labels = {c["key"]: c for c in trade_checklists.checklist_for(trade)}
    out: List[Dict[str, Any]] = []
    for i, ln in enumerate(lines):
        comp = _norm(ln.get("component")).replace(" ", "_")[:60]
        if not comp:
            raise BuildupError("Every line needs what it is (the component).")
        qty = _num(ln.get("qty_per_unit"))
        if qty is None or qty <= 0:
            raise BuildupError(f"{ln.get('label') or comp}: the quantity must be more than 0.")
        waste = _num(ln.get("waste_pct") or 0)
        if waste is None or waste < 0 or waste >= 100:
            raise BuildupError(f"{ln.get('label') or comp}: waste must be a percentage from 0 to 99.")
        kind = (ln.get("kind") or labels.get(comp, {}).get("kind") or "material").lower()
        if kind not in KINDS:
            raise BuildupError(f"{comp}: kind must be material, labour or plant.")
        unit = str(ln.get("unit") or labels.get(comp, {}).get("unit") or "").strip()[:20]
        if not unit:
            raise BuildupError(f"{ln.get('label') or comp}: say the unit (e.g. each, bag, m3, hour).")
        out.append({"position": i, "component": comp,
                    "label": str(ln.get("label") or labels.get(comp, {}).get("label") or comp.replace("_", " "))[:120],
                    "kind": kind, "qty_per_unit": str(qty.quantize(Decimal("0.0001"))), "unit": unit,
                    "waste_pct": str(waste.quantize(Decimal("0.01"))),
                    "note": (str(ln.get("note") or "")[:300] or None),
                    "source_ref": (str(ln.get("source_ref") or "")[:20] or None)})
    if not out:
        raise BuildupError("A build-up needs at least one line.")
    return out


def _insert(tenant_id: str, row: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    db = _db()
    got = db.table(TABLE).insert({**row, "tenant_id": tenant_id}).execute().data or []
    if not got:
        raise BuildupError("The build-up didn't save — nothing changed. Please try again.")
    bid = got[0]["id"]
    db.table(LINES).insert([{**ln, "buildup_id": bid, "tenant_id": tenant_id} for ln in lines]).execute()
    back = get(tenant_id, bid)
    if not back or len(back["lines"]) != len(lines):
        raise BuildupError("The build-up didn't save completely — please try again.")
    return back


def _versions(tenant_id: str, key: str) -> List[Dict[str, Any]]:
    return _db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("item_key", key).execute().data or []


def save_proposed(tenant_id: str, *, trade: str, item: str, spec: Optional[Dict[str, Any]], unit: str,
                  lines: Iterable[Dict[str, Any]], sources: List[Dict[str, Any]],
                  unknown: List[Dict[str, Any]], created_by: str = "") -> Dict[str, Any]:
    spec = clean_spec(spec)
    key = item_key(item, spec)
    version = 1 + max([int(r.get("version") or 0) for r in _versions(tenant_id, key)] or [0])
    row = {"trade": trade, "item": item.strip()[:160], "item_key": key, "unit": unit, "spec": spec,
           "status": "proposed", "version": version, "sources": sources, "unknown": unknown,
           "shared": True, "created_by": created_by or None}
    return _insert(tenant_id, row, _clean_lines(lines, trade))


def _apply_changes(lines: List[Dict[str, Any]], changes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by = {ln["component"]: dict(ln) for ln in lines}
    order = [ln["component"] for ln in lines]
    for ch in changes or []:
        comp = _norm(ch.get("component")).replace(" ", "_")
        if not comp:
            continue
        if ch.get("remove"):
            by.pop(comp, None)
            continue
        cur = by.get(comp, {"component": comp})
        for f in ("qty_per_unit", "unit", "waste_pct", "label", "kind", "note"):
            if ch.get(f) not in (None, ""):
                cur[f] = ch[f]
        cur["source_ref"] = "confirmed by owner" if comp not in by else cur.get("source_ref")
        if comp not in by:
            order.append(comp)
        by[comp] = cur
    return [by[c] for c in order if c in by]


def confirm(tenant_id: str, buildup_id: str, *, person: str, full_access: bool,
            changes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """An owner or manager confirms a build-up. Plain confirm of this business's own proposed one
    marks it confirmed; a change — or confirming another business's shared one — saves this
    business's own new version. Whatever it replaces is retired. Read back."""
    if not full_access:
        raise BuildupError("Only an owner or manager can confirm a build-up — they'll be asked.")
    b = get(tenant_id, buildup_id)
    if not b:
        raise BuildupError("I can't find that build-up.")
    if b.get("status") == "retired":
        raise BuildupError("That build-up has been replaced — look up the current one.")
    db = _db()
    now = _now()
    if b["origin"] == "proposed" and not changes:
        db.table(TABLE).update({"status": "confirmed", "confirmed_by": person or None,
                                "confirmed_at": now}).eq("id", buildup_id).eq("tenant_id", tenant_id).execute()
        new = get(tenant_id, buildup_id)
        if not new or new.get("status") != "confirmed":
            raise BuildupError("The confirmation didn't save — please try again.")
    elif b["origin"] == "own" and not changes:
        return b                                  # already confirmed, nothing to do
    else:
        lines = _apply_changes([{k: v for k, v in ln.items() if k not in ("id", "buildup_id", "tenant_id")}
                                for ln in b["lines"]], changes or [])
        key = b["item_key"]
        version = 1 + max([int(r.get("version") or 0) for r in _versions(tenant_id, key)] or [0])
        present = {ln["component"] for ln in lines}
        row = {"trade": b["trade"], "item": b["item"], "item_key": key, "unit": b["unit"],
               "spec": b.get("spec") or {}, "status": "confirmed", "version": version,
               "sources": b.get("sources") or [],
               "unknown": [u for u in (b.get("unknown") or []) if u.get("key") not in present],
               "shared": True, "based_on": b["id"], "created_by": person or None,
               "confirmed_by": person or None, "confirmed_at": now}
        new = _insert(tenant_id, row, _clean_lines(lines, b["trade"]))
    # retire what it replaces: this business's other proposed/confirmed versions of the same item
    for r in _versions(tenant_id, new["item_key"]):
        if r["id"] != new["id"] and r.get("status") in ("proposed", "confirmed"):
            db.table(TABLE).update({"status": "retired"}).eq("id", r["id"]).eq("tenant_id", tenant_id).execute()
    return new


def retire(tenant_id: str, buildup_id: str, *, full_access: bool) -> bool:
    if not full_access:
        raise BuildupError("Only an owner or manager can remove a build-up.")
    b = get(tenant_id, buildup_id)
    if not b or b["origin"] == "shared":
        raise BuildupError("I can't find that build-up.")
    _db().table(TABLE).update({"status": "retired"}).eq("id", buildup_id).eq("tenant_id", tenant_id).execute()
    back = get(tenant_id, buildup_id)
    return bool(back and back.get("status") == "retired")


# ── showing it ───────────────────────────────────────────────────────────────

_ORIGIN = {"own": "confirmed", "shared": "confirmed by another Vula business",
           "proposed": "researched, waiting for an owner or manager to confirm", "retired": "replaced"}


def describe(b: Dict[str, Any]) -> str:
    """Plain text for WhatsApp or the dashboard. Quantities exactly as stored."""
    head = f"{b['item']}" + (f" ({spec_text(b.get('spec'))})" if b.get("spec") else "")
    out = [f"{head}, per {b['unit']}, {b['trade']}: {_ORIGIN.get(b.get('origin'), b.get('status'))}"]
    srcs = {str(s.get("n")): s for s in (b.get("sources") or [])}
    for ln in b.get("lines") or []:
        waste = _num(ln.get("waste_pct")) or Decimal(0)
        src = srcs.get(str(ln.get("source_ref") or "").strip("[]"))
        where = f" [{src.get('title') or src.get('url')}]" if src else (
            " (set by owner)" if ln.get("source_ref") == "confirmed by owner" else "")
        out.append(f"• {ln['label']}: {_show(ln['qty_per_unit'])} {ln['unit']}"
                   + (f" + {_show(waste)}% waste" if waste else "") + where)
    unknown = [u.get("label") or u.get("key") for u in (b.get("unknown") or [])]
    if unknown:
        out.append("Not found in the sources, needs a figure: " + ", ".join(unknown))
    if b.get("sources"):
        out.append("Sources: " + " ".join(f"[{s.get('n')}] {s.get('url')}" for s in b["sources"]))
    return "\n".join(out)


def public(b: Dict[str, Any]) -> Dict[str, Any]:
    """For the API: no other business's ids or people."""
    keep = ("id", "trade", "item", "unit", "spec", "status", "version", "sources", "unknown",
            "origin", "confirmed_at", "created_at", "based_on")
    out = {k: b.get(k) for k in keep}
    out["lines"] = [{k: ln.get(k) for k in ("component", "label", "kind", "qty_per_unit", "unit",
                                            "waste_pct", "note", "source_ref")} for ln in b.get("lines") or []]
    out["text"] = describe(b)
    return out


# ── research ─────────────────────────────────────────────────────────────────

# A thousands group never starts with 0: "1 250" is 1250, "0,017" is 0.017.
_NUM_RE = re.compile(r"(?<![\d.,])[1-9]\d{0,2}(?:[ ,]\d{3})+(?:\.\d+)?(?![\d.,]\d)|(?<![\d.,])\d+(?:[.,]\d+)?(?![\d])")


def numbers_in(text: str) -> set:
    """Every number printed in `text`, as normalised Decimals ("1 250", "0,017", "0.5")."""
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0)
        if re.fullmatch(r"[1-9]\d{0,2}(?:[ ,]\d{3})+(?:\.\d+)?", tok):
            tok = re.sub(r"[ ,]", "", tok)
        else:
            tok = tok.replace(",", ".")
        d = _num(tok)
        if d is not None:
            out.add(d.normalize())
    return out


def grounded_lines(items: Any, pages: List[Dict[str, str]], checklist: List[Dict[str, str]]):
    """(lines, unknown, used page numbers). A line is kept only when its quantity is printed on
    the page it cites; a stated waste % must be printed there too (else 0, with a note)."""
    keys = {c["key"]: c for c in checklist}
    lines: Dict[str, Dict[str, Any]] = {}
    used: set = set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        comp = _norm(it.get("component")).replace(" ", "_")
        if comp not in keys or comp in lines:
            continue
        try:
            n = int(str(it.get("source")).strip("[] "))
        except ValueError:
            continue
        if not 1 <= n <= len(pages):
            continue
        qty = _num(it.get("qty_per_unit"))
        printed = numbers_in(pages[n - 1].get("text") or "")
        if qty is None or qty <= 0 or qty.normalize() not in printed:
            continue
        waste = _num(it.get("waste_pct"))
        note = str(it.get("note") or "")[:200]
        if waste is not None and waste > 0 and waste.normalize() not in printed:
            waste, note = None, (note + " (waste not stated in the source)").strip()
        lines[comp] = {"component": comp, "label": keys[comp]["label"], "kind": keys[comp]["kind"],
                       "qty_per_unit": qty, "unit": str(it.get("unit") or keys[comp]["unit"])[:20],
                       "waste_pct": waste or 0, "note": note or None, "source_ref": str(n)}
        used.add(n)
    unknown = [{"key": c["key"], "label": c["label"]} for c in checklist if c["key"] not in lines]
    ordered = [lines[c["key"]] for c in checklist if c["key"] in lines]
    return ordered, unknown, used


def _parse(text: str) -> Any:
    from vula.takeoff.construction_rates_scraper import _parse_json_array
    return _parse_json_array(re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip())


async def research(tenant_id: str, item: str, spec: Optional[Dict[str, Any]] = None, unit: str = "m2",
                   trade: Optional[str] = None, created_by: str = "") -> Dict[str, Any]:
    """Research a build-up for the item from cited pages and save it as proposed. The search
    carries the item and spec only — never a client, a project or a person."""
    from vula import research as web
    trade = trade or trade_checklists.trade_of_item(f"{item} {spec_text(spec)}")
    checklist = trade_checklists.checklist_for(trade)
    if not checklist:
        raise BuildupError("I can't tell which trade that work is — say what it is, e.g. "
                           "\"220 mm brick wall\" or \"plaster to brick walls\".")
    what = f"{item}" + (f" ({spec_text(spec)})" if spec else "")
    question = (f"Quantities of materials and labour per {unit} for {what} in South Africa: "
                + ", ".join(c["label"] for c in checklist))
    queries = await web.plan_queries(question)
    pages = await web.read_pages(await web._search(queries))
    if not pages:
        raise BuildupError("I couldn't read any sources for that just now — try again later.")
    numbered = "\n\n".join(f"[{i}] {p.get('title') or ''} ({p.get('url')})\n{(p.get('text') or '')[:3000]}"
                           for i, p in enumerate(pages, 1))
    comps = "\n".join(f"- {c['key']}: {c['label']} (usually per {c['unit']})" for c in checklist)
    messages = [{"role": "user", "content": (
        f"From the numbered sources below, give the quantities of each component needed for ONE "
        f"{unit} of: {what}.\nComponents:\n{comps}\n\n"
        "Rules: use only quantities printed in a source and copy the number exactly as printed; "
        "cite the source number; leave out a component no source gives a figure for. Answer with "
        'a JSON array only: [{"component": "units", "qty_per_unit": 104, "unit": "each", '
        '"waste_pct": 5, "source": 2, "note": "NFX bricks, 10 mm joints"}]\n\n'
        f"Sources:\n{numbered}")}]

    def checks_out(text: str) -> bool:
        return bool(grounded_lines(_parse(text), pages, checklist)[0])

    raw = await web._complete("buildup_research", messages, max_tokens=1200, timeout_s=90.0, accept=checks_out)
    lines, unknown, used = grounded_lines(_parse(raw), pages, checklist)
    if not lines:
        raise BuildupError("I couldn't find quantities I could check in the sources for that — "
                           "an owner or manager can enter the build-up instead.")
    sources = [{"n": i, "title": (pages[i - 1].get("title") or "")[:140], "url": pages[i - 1].get("url")}
               for i in sorted(used)]
    log.info("build-up researched for %s (%s): %d grounded, %d unknown", tenant_id, trade, len(lines), len(unknown))
    return save_proposed(tenant_id, trade=trade, item=item, spec=spec, unit=unit, lines=lines,
                         sources=sources, unknown=unknown, created_by=created_by)


def to_json(b: Dict[str, Any]) -> str:
    return json.dumps(public(b), default=str)
