"""vula/commerce/stock_sheet.py — read a distributor stock sheet as ROWS, not prose.

2026-09-07. Gerflor's rep uploaded DT SOH and Planning 07.09.26.pdf and asked "How stocks
creations" five minutes later. Vula said it couldn't find any information, and four rounds of
retrieval tuning never fixed it — because the honest answer was not in the document:

    "Creation" does not appear in that stock sheet at all.

The sheet covers VIRTUO, ATLAS, MAC TILES, AMBIANCE ULTRA, EL7 SD ROBUST and others. The useful
reply is "Creation isn't on the current DT stock list — here's what is", and **similarity search
can never say that**: an embedding can rank what a document contains, never report what it
omits. Absence is a fact about a SET, so the sheet has to be read as a set.

The rows look like this (the '|' appears only in some extractions of the same table):

    VIRTUO 30 COL: SUNNY WHITE 2.00MM 532
    VIRTUO 30 COL: BAITA MEDIUM 2.00MM 581.4 534 Est. Mid Nov - TBC
    MAC TILES-COL: 612 ST/GREY 1.60MM 6504.3
    AMBIANCE ULTRA TERRA COL: 0203 STEAM GREY 2.00W | 540 | 2000 | Est. Mid Oct - TBC
    MAC TILES-COL: 656 BASIL 2.00MM 378 164m² Reserved TVET College
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# "2.00MM", "1.60MM", "2.00W", "2.50 mm" — the thickness that separates the product/colour from
# the quantities. Anchored on it because product names and colours both contain spaces and
# digits, and only the thickness reliably marks where the numbers start.
_THICK = re.compile(r"\b(\d+[.,]?\d*)\s*(MM|W)\b", re.I)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")

# A sheet is only recognised when its own header is present, so an unrelated PDF full of numbers
# is never parsed as stock.
_HEADER_HINTS = ("soh", "stock on hand")

# Words that mean the question is about stock/availability, so an "X is not on the list"
# answer is genuinely useful rather than a non-sequitur hijacking an unrelated KB lookup.
_STOCK_INTENT = re.compile(
    r"\b(stock|soh|in stock|out of stock|available|availab|on hand|do we have|"
    r"have any|how much .* (left|do we have)|how many|lead time|inbound|eta|"
    r"back ?order|when .* (arrive|available)|m2|m²|square met)\b", re.I)


def looks_like_stock_sheet(text: str) -> bool:
    low = (text or "").lower()
    if not any(h in low for h in _HEADER_HINTS):
        return False
    # A header alone is not enough — there must be rows shaped like stock lines.
    return len(_THICK.findall(text or "")) >= 5


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("|", " ")).strip(" -:•❗")


def parse_stock_lines(text: str) -> List[Dict[str, Any]]:
    """Every stock row we can read out of `text`. Unparseable lines are skipped, never guessed.

    A row is only emitted when a thickness marker is found AND a quantity follows it, because
    those two together are what distinguish a stock line from a heading or a project note.
    """
    rows: List[Dict[str, Any]] = []
    for raw in re.split(r"[\r\n]+", text or ""):
        line = raw.strip()
        if not line or len(line) < 8:
            continue
        m = _THICK.search(line)
        if not m:
            continue
        head = _clean(line[:m.start()])
        tail = line[m.end():]
        if not head:
            continue

        nums = _NUM.findall(tail.replace("m²", " "))
        if not nums:
            continue

        # Product / colour split on the sheet's own "COL:" marker when it is there.
        if re.search(r"\bCOL\b\s*:?", head, re.I):
            product, colour = re.split(r"\bCOL\b\s*:?", head, maxsplit=1, flags=re.I)
        else:
            product, colour = head, ""
        product = _clean(product)
        colour = _clean(colour)
        if not product:
            continue

        def _f(v: str) -> Optional[float]:
            try:
                return float(v.replace(",", "."))
            except Exception:
                return None

        soh = _f(nums[0])
        inbound = _f(nums[1]) if len(nums) > 1 else None
        # Anything after the numbers is the ETA / reservation note, kept verbatim: "Reserved
        # TVET College" and "Est. Mid Nov - TBC" are exactly what a rep needs to see.
        note = _clean(re.sub(r"^[\d\s.,|m²]+", "", tail))
        rows.append({
            "product": product,
            "colour": colour,
            "thickness": f"{m.group(1)}{m.group(2).upper()}",
            "soh": soh,
            "inbound": inbound,
            "note": note or None,
        })
    return rows


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Non-product words a rep wraps a stock question in — dropped before token matching so
# "how's virtuo stock" matches VIRTUO rather than matching nothing (whole-string substring) or
# everything (the word "stock").
_QUERY_NOISE = {
    "stock", "soh", "have", "has", "any", "how", "hows", "much", "many", "our", "ours", "the",
    "there", "is", "are", "do", "does", "we", "us", "in", "of", "on", "at", "for", "left",
    "need", "want", "get", "got", "a", "an", "whats", "what", "which", "some", "still",
    "available", "availability", "hand", "onhand", "inbound", "eta", "lead", "time", "when",
    "arrive", "arriving", "coming", "order", "reserved", "please", "check", "tell", "me",
    "about", "list", "show", "level", "levels", "qty", "quantity",
}


def _stem(tok: str) -> str:
    return tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok


def find_product(rows: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Rows whose product or colour matches `query`. Tries a whole-string substring first
    ("mac tiles" → "MAC TILES-COL"), then falls back to matching the query's distinctive
    tokens ("how's virtuo stock" → VIRTUO), so a natural-language question still lands."""
    q = _norm(query)
    if not q:
        return []
    stems = {q, _stem(q)}
    tokens = {_stem(t) for t in q.split() if len(t) >= 3 and t not in _QUERY_NOISE}
    out = []
    for r in rows:
        hay = _norm(f"{r.get('product', '')} {r.get('colour', '')}")
        hay_tokens = {_stem(t) for t in hay.split()}
        if any(s and s in hay for s in stems) or (tokens and tokens & hay_tokens):
            out.append(r)
    return out


def product_names(rows: List[Dict[str, Any]]) -> List[str]:
    """The distinct product ranges on the sheet — what to offer when the asked-for one is
    absent. This list IS the answer to "is Creation stocked?" when Creation is not on it."""
    seen: List[str] = []
    for r in rows:
        # "VIRTUO 30" / "MAC TILES" — the range, not every colour variant.
        name = re.sub(r"[-–]\s*$", "", (r.get("product") or "")).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def summarise(rows: List[Dict[str, Any]], query: str, as_at: str = "") -> Dict[str, Any]:
    """A deterministic answer about one product's stock, INCLUDING when it is not listed.

    The absence case is the whole reason this module exists: "Creation isn't on the DT stock
    list" is true, useful and unobtainable from semantic search.
    """
    matches = find_product(rows, query)
    if not matches:
        return {
            "found": False,
            "query": query,
            "as_at": as_at or None,
            "available_products": product_names(rows)[:20],
            "answer": (f"{query} is not on this stock list"
                       + (f" (as at {as_at})" if as_at else "") + "."),
        }
    in_stock = [r for r in matches if (r.get("soh") or 0) > 0]
    total = round(sum(r.get("soh") or 0 for r in matches), 2)
    return {
        "found": True,
        "query": query,
        "as_at": as_at or None,
        "line_count": len(matches),
        "in_stock_lines": len(in_stock),
        "total_soh": total,
        "rows": matches[:25],
        "answer": (f"{len(matches)} {query} line(s) on the sheet, {len(in_stock)} with stock, "
                   f"{total:g} m² in total" + (f" (as at {as_at})" if as_at else "") + "."),
    }


def as_at_date(text: str) -> str:
    """The sheet's own 'SOH m² – 07.09.26' date, so an answer can say how current it is."""
    m = re.search(r"SOH[^\n]*?(\d{2}[./]\d{2}[./]\d{2,4})", text or "", re.I)
    return m.group(1) if m else ""


# ── Persistence ───────────────────────────────────────────────────────────────
# A stock sheet is structured data, not prose, so it is stored as rows (vula_stock_sheets,
# migration 156) and answered deterministically — never left to similarity search, which is
# exactly what could not report "Creation isn't on the list" in the first place.

def _client():
    from vula.commerce import service
    return service._client()


def persist_if_stock_sheet(tenant_id: str, doc_id: str, filename: str, text: str) -> Optional[Dict[str, Any]]:
    """If `text` reads as a distributor stock sheet, parse it and upsert the rows for this
    tenant. Returns a short summary dict when stored, else None (caller then treats the upload
    as an ordinary document)."""
    if not looks_like_stock_sheet(text):
        return None
    rows = parse_stock_lines(text)
    if len(rows) < 5:
        return None
    as_at = as_at_date(text)
    ranges = product_names(rows)
    try:
        from datetime import datetime, timezone
        _client().table("vula_stock_sheets").upsert({
            "tenant_id": tenant_id,
            "doc_id": doc_id,
            "filename": filename or "stock sheet",
            "as_at": as_at or None,
            "rows": rows,
            "product_ranges": ranges,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="tenant_id,doc_id").execute()
    except Exception as exc:  # noqa: BLE001 — filing must not fail on a storage hiccup
        log.warning("stock sheet persist failed for %s/%s: %s", tenant_id, filename, exc)
        return None
    log.info("stock sheet stored: tenant=%s file=%s rows=%d ranges=%d",
             tenant_id, filename, len(rows), len(ranges))
    return {"stored": True, "line_count": len(rows), "as_at": as_at,
            "product_ranges": ranges[:20]}


def latest_rows_for_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
    """The most recently updated stored stock sheet for this tenant, or None."""
    try:
        got = (_client().table("vula_stock_sheets")
               .select("filename,as_at,rows,product_ranges,updated_at")
               .eq("tenant_id", tenant_id)
               .order("updated_at", desc=True).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("stock sheet lookup skipped for %s: %s", tenant_id, exc)
        return None
    return got[0] if got else None


def answer_stock_query(tenant_id: str, query: str) -> Optional[Dict[str, Any]]:
    """Deterministic stock answer for `query` from this tenant's latest stored sheet, or None
    when the tenant has no stock sheet on file. The absence case ("X is not on this list —
    here's what is") is the whole reason this exists."""
    sheet = latest_rows_for_tenant(tenant_id)
    if not sheet:
        return None
    rows = sheet.get("rows") or []
    result = summarise(rows, query, as_at=sheet.get("as_at") or "")
    # A product match is unambiguous — always answer. A non-match ("not on this list") is only
    # returned when the question is actually about stock; otherwise a stock sheet on file would
    # hijack every unrelated KB lookup ("what's our VAT number") with a nonsense "not on the
    # stock list" reply.
    if not result.get("found") and not _STOCK_INTENT.search(query or ""):
        return None
    result["source_file"] = sheet.get("filename")
    return result
