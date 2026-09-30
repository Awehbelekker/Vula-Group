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
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?")

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


def _qty(v: str) -> Optional[float]:
    """'1,949.4' → 1949.4 (thousands comma), '581,4' → 581.4 (decimal comma), '532' → 532."""
    v = (v or "").strip()
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", v):
        v = v.replace(",", "")
    else:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("|", " ")).strip(" -:•❗")


def parse_stock_lines(text: str) -> List[Dict[str, Any]]:
    """Every stock row we can read out of `text`. Unparseable lines are skipped, never guessed.

    A row is only emitted when a thickness marker is found AND a quantity follows it, because
    those two together are what distinguish a stock line from a heading or a project note.
    """
    rows: List[Dict[str, Any]] = []
    block = _parse_block_layout(text)
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

        soh = _qty(nums[0])
        inbound = _qty(nums[1]) if len(nums) > 1 else None
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
    seen = {(r["product"], r["colour"]) for r in block}
    return block + [r for r in rows if (r["product"], r["colour"]) not in seen]


# 2026-09-30, DT_Weekly_SOH_Stock_Availability_29-09-2026.pdf: the new weekly sheet puts every
# field on its own line — the line-per-row parser above read 6 of ~100 products:
#
#     VIRTUO 55 COL: DAINTREE BROWN 2.50MM
#     99.2
#     ◆ PROJECT ALLOCATED
#     300.0
#     ❗Est. End October
#     150m² Reserved for Tstisikama
#
# A product line ends at its thickness (a long name can wrap, leaving the thickness alone on the
# next line); the lines up to the next product are its quantity, status, inbound, ETA and notes.
_STATUS = re.compile(r"(IN STOCK|OUT OF STOCK|INBOUND|PROJECT ALLOCATED|VERIFY SOH)", re.I)
_QTY_LINE = re.compile(r"^\d{1,3}(?:,\d{3})*(?:[.,]\d+)?$|^\d+(?:[.,]\d+)?$")
_ENDS_THICK = re.compile(r"\b(\d+[.,]?\d*)\s*(MM|W)\s*$", re.I)


def _parse_block_layout(text: str) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]
    rows: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        name, thick = None, None
        m = _ENDS_THICK.search(line)
        if m and "COL" in line.upper():
            name, thick = line[:m.start()], m
        elif "COL" in line.upper() and i + 1 < len(lines):
            m2 = re.fullmatch(r"(\d+[.,]?\d*)\s*(MM|W)", lines[i + 1], re.I)
            if m2:
                name, thick = line, m2
                i += 1
        if not name or i + 1 >= len(lines) or not _QTY_LINE.match(lines[i + 1]):
            i += 1
            continue
        product, colour = (re.split(r"\bCOL\b\s*:?", name, maxsplit=1, flags=re.I) + [""])[:2]
        row: Dict[str, Any] = {"product": _clean(product), "colour": _clean(colour),
                               "thickness": f"{thick.group(1)}{thick.group(2).upper()}",
                               "soh": _qty(lines[i + 1]), "inbound": None, "status": None, "note": None}
        j = i + 2
        notes: List[str] = []
        while j < len(lines) and not (_ENDS_THICK.search(lines[j]) and "COL" in lines[j].upper()) \
                and not ("COL" in lines[j].upper() and j + 1 < len(lines)
                         and re.fullmatch(r"(\d+[.,]?\d*)\s*(MM|W)", lines[j + 1], re.I)):
            ln = lines[j]
            st = _STATUS.search(ln)
            if st and row["status"] is None and len(ln) < 30:
                row["status"] = st.group(1).lower()
            elif _QTY_LINE.match(ln) and row["inbound"] is None and row["status"]:
                row["inbound"] = _qty(ln)
            elif row["status"] and (ln.lower().lstrip("❗ ").startswith("est") or "reserved" in ln.lower()):
                notes.append(_clean(ln))
            else:
                break          # a heading or the legend — this row is done
            j += 1
        row["note"] = "; ".join(notes) or None
        if row["product"]:
            rows.append(row)
        i = j
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
    "square", "metre", "metres", "meter", "meters", "sqm", "m2", "stocks",
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
    # 2026-09-30 (DT weekly sheet): "Virtuo 55 Daintree Brown" matched all 33 VIRTUO lines —
    # any shared word counted. Rank by how many of the question's words a line carries and keep
    # only the best, so a colour question gets that colour and a range question the whole range.
    scored = []
    for r in rows:
        hay = _norm(f"{r.get('product', '')} {r.get('colour', '')}")
        hay_tokens = {_stem(t) for t in hay.split()}
        if any(s and s in hay for s in stems):
            scored.append((len(tokens) + 1, r))
        elif tokens and tokens & hay_tokens:
            # a two-word phrase from the question ("lt blue", "spring green") beats single words
            pairs = sum(1 for a, b in zip(q.split(), q.split()[1:]) if f"{a} {b}" in hay)
            scored.append((len(tokens & hay_tokens) + 0.5 * pairs, r))
    if not scored:
        return []
    best = max(sc for sc, _ in scored)
    return [r for sc, r in scored if sc == best]


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


def _row_line(r: Dict[str, Any]) -> str:
    """'VIRTUO 55 DAINTREE BROWN: 99.2 m² (project allocated), 300 m² inbound, Est. End October;
    150m² Reserved for Tstisikama' — what a rep needs in one line."""
    name = " ".join(x for x in (r.get("product"), r.get("colour")) if x)
    soh = r.get("soh")
    bits = [f"{name}: {soh:g} m²" if soh is not None else name]
    if r.get("status"):
        bits[0] += f" ({r['status']})"
    if r.get("inbound"):
        bits.append(f"{r['inbound']:g} m² inbound")
    if r.get("note"):
        bits.append(r["note"])
    return ", ".join(bits)


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
            "answer": (f"{' '.join(t for t in query.split() if _norm(t) not in _QUERY_NOISE and len(_norm(t)) > 1).strip(' ?.') or query} is not on this stock list"
                       + (f" (as at {as_at})" if as_at else "") + "."),
        }
    # "Mac tiles basil" when Basil isn't on the sheet: say so, then what the range does have —
    # never a list of every Mac Tiles line as if one of them were the answer.
    q_tokens = [t for t in _norm(query).split() if len(t) >= 3 and t not in _QUERY_NOISE]
    matched_text = " ".join(_norm(f"{r.get('product', '')} {r.get('colour', '')}") for r in matches)
    missing = [t for t in q_tokens if _stem(t) not in {_stem(x) for x in matched_text.split()}
               and t not in {"square", "metre", "metres", "meter", "meters"} and not t.isdigit()]
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
        "not_listed": missing or None,
        "answer": ((f"{' '.join(missing).upper()} isn't on this stock list. What is: " if missing else "")
                   + "; ".join(_row_line(r) for r in matches[:8])
                   + (f" (+{len(matches) - 8} more)" if len(matches) > 8 else "")
                   + (f" — as at {as_at}" if as_at else "") + "."),
    }


def as_at_date(text: str) -> str:
    """The sheet's own 'SOH m² – 07.09.26' date, so an answer can say how current it is."""
    m = re.search(r"SOH[^\n]*?(\d{2}[./]\d{2}[./]\d{2,4})", text or "", re.I)
    if m:
        return m.group(1)
    # "Stock position as at 29 September 2026" (DT weekly sheet, 2026-09-30)
    m = re.search(r"as at\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text or "", re.I)
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
