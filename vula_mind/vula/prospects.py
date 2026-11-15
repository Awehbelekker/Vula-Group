"""Prospects: a business the Vula team would like as a client, scanned before it is one.

13 Nov 2026 (Ian: "scan any client's website and social and do a pitch"). The website import
could only run for a tenant, so the only way to look at a prospect from Master was to create a
real, active tenant for them. A prospect is scanned with the import's read-only mode
(site_import.inspect_site: nothing saved anywhere) and the brand suggestion, and the report is
built in code from what was actually found — every figure and every gap is a count or an
address from the scan, never a model's reading. "Make them a tenant" then hands the website to
the ordinary new-tenant flow, which imports it for real.

Social pages are recorded as links only: nothing here reads Instagram, Facebook or TikTok.

Stored in vula_prospects (migration 268), read and written by Master only.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

TABLE = "vula_prospects"
STALE_AFTER = timedelta(minutes=20)       # a scan still "running" this long was lost to a restart
_SOCIAL = re.compile(r"(?i)^(https?://)?([a-z0-9-]+\.)*(instagram|facebook|fb|tiktok|linkedin|youtube|x|twitter)\.(com|me)/")
POLICY_LABELS = {"terms": "terms and conditions", "privacy": "privacy policy",
                 "returns": "returns or refund policy", "shipping": "delivery or shipping policy"}
CONTACT_LABELS = {"phone": "a phone number", "email": "an email address", "address": "an address",
                  "hours": "opening hours", "socials": "social links"}


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_page(link: str) -> str:
    return re.sub(r"^https?://(www\.|m\.)?", "", link.lower()).rstrip("/")


def clean_socials(raw: Any) -> List[str]:
    """Social page links: http(s) addresses on the known social sites, de-duplicated (with or
    without "www."), from a list or a comma/space-separated string."""
    items = raw if isinstance(raw, list) else [raw]
    out: List[str] = []
    for item in items:
        for s in re.split(r"[\s,]+", str(item or "")):
            s = s.strip()
            if s and _SOCIAL.match(s):
                s = s if s.lower().startswith("http") else "https://" + s
                if _same_page(s) not in {_same_page(o) for o in out}:
                    out.append(s.rstrip("/"))
    return out[:10]


def _rands(cents: int) -> str:
    return f"R{cents / 100:,.0f}".replace(",", " ")


# ── the report ───────────────────────────────────────────────────────────────────────────────

def build_report(st: Dict[str, Any], brand: Optional[Dict[str, Any]] = None,
                 socials: Optional[List[str]] = None, name: str = "") -> Dict[str, Any]:
    """What the scan found, what's missing on their site, what Vula would do for them, and a
    pitch to read out — all from the scan's own counts. `st` is site_import.inspect_site's
    result."""
    cov = st.get("coverage") or {}
    pr, pol, con, pg = (cov.get("products") or {}), (cov.get("policies") or {}), \
        (cov.get("contact") or {}), (cov.get("pages") or {})
    facts = st.get("facts") or {}
    products = st.get("products_preview") or []
    site = urlparse(st.get("url") or "").netloc or (st.get("url") or "")
    name = name or facts.get("name") or site
    kind = "shop" if pr.get("found") else "services"

    found: List[str] = []
    gaps: List[str] = []
    if st.get("error"):
        gaps.append(f"The website couldn't be read: {st['error']}")
    if pg.get("read"):
        found.append(f"{pg.get('used', 0)} pages with content (of {pg.get('found', 0)} found"
                     f"{', from the sitemap' if pg.get('how') == 'sitemap' else ''})")
    if st.get("note"):
        gaps.append(st["note"] + " Search engines and AI assistants see as little of it.")
    if pr.get("found"):
        found.append(f"{pr['found']} products, {pr.get('with_price', 0)} with a price"
                     + (f" ({st['platform']})" if st.get("platform") not in ("", "website", None) else ""))
        sized = [p for p in products if p.get("options", 0) > 1]
        if sized:
            spread = [p for p in sized if p["price_max_cents"] > p["price_min_cents"]]
            found.append(f"{len(sized)} sold in sizes or options"
                         + (f", {len(spread)} at different prices"
                            f" (e.g. {spread[0]['name']}: {_rands(spread[0]['price_min_cents'])}–"
                            f"{_rands(spread[0]['price_max_cents'])})" if spread else ""))
        for n, what in ((pr["found"] - pr.get("with_image", 0), "without a photo"),
                        (pr["found"] - pr.get("with_description", 0), "without a description"),
                        (pr["found"] - pr.get("with_price", 0), "without a price on the site")):
            if n > 0:
                gaps.append(f"{n} of {pr['found']} products {what}")
    present = [POLICY_LABELS[k] for k in POLICY_LABELS if pol.get(k)]
    missing = [POLICY_LABELS[k] for k in POLICY_LABELS if not pol.get(k)]
    if present:
        found.append("Their " + ", ".join(present))
    if missing and pg.get("read"):
        gaps.append("No " + " or ".join(missing) + " on the website")
    contact = [CONTACT_LABELS[k] for k in CONTACT_LABELS if con.get(k)]
    no_contact = [CONTACT_LABELS[k] for k in CONTACT_LABELS if not con.get(k)]
    if contact:
        found.append("Contact details: " + ", ".join(contact))
    if no_contact and pg.get("read"):
        gaps.append("Not on the website: " + ", ".join(no_contact))
    if (brand or {}).get("accent_color"):
        found.append(f"Brand colours from {'their logo' if brand.get('logo_url') else 'the site'}")

    all_socials = clean_socials([*(socials or []), *(facts.get("socials") if isinstance(facts.get("socials"), list)
                                                    else [facts.get("socials") or ""])])

    if kind == "shop":
        offer = [
            "A WhatsApp shop: customers browse product cards and a catalogue, and order in the chat"
            + (" — every size at its own price, colours and styles asked for and kept on the order"
               if any(p.get("options", 0) > 1 or p.get("choices") for p in products) else ""),
            "Payment links and invoices from the order; the owner sees every order in one dashboard",
            "Customer questions answered 24/7 from their own "
            + (", ".join(present) if present else "pages") + ", handed to a person when Vula isn't sure",
            f"Their {pr['found']} products and {pg.get('used', 0)} pages move into Vula in minutes — this "
            "scan is that import, without saving",
        ]
    else:
        offer = [
            f"A WhatsApp assistant that answers prospects and customers 24/7 from their own "
            f"{pg.get('used', 0)} pages" + (f" ({', '.join(present)})" if present else "")
            + ", in their voice, and hands over to a person when it isn't sure",
            "Leads captured from every chat, with follow-ups so none go cold",
            "Quotes, invoices and payment links from the chat; every client's history in one record",
            "Set up from this website in minutes — this scan is that import, without saving",
        ]
    fix = gaps[:4]
    pitch = "\n".join([
        f"{name}: what Vula found on {site}",
        *[f"• {x}" for x in found[:6]],
        *(["", "What we'd fix:", *[f"• {x}" for x in fix]] if fix else []),
        "", "With Vula:", *[f"• {x}" for x in offer],
    ])
    return {"name": name, "site": site, "kind": kind, "found": found, "gaps": gaps, "offer": offer,
            "pitch": pitch, "socials": all_socials, "products": products[:24],
            "pages": (st.get("pages_preview") or [])[:40],
            "brand": {k: (brand or {}).get(k) for k in ("accent_color", "secondary_color", "ink_color", "logo_url",
                                                         "palette") if (brand or {}).get(k)},
            "facts": {k: v for k, v in facts.items() if k in ("name", "phone", "email", "address", "hours")},
            "counts": {"pages": pg.get("used", 0), "products": pr.get("found", 0),
                       "policies": len(present), "contact": len(contact)}}


# ── scanning and storage ─────────────────────────────────────────────────────────────────────

async def scan(url: str, socials: Optional[List[str]] = None, name: str = "") -> Dict[str, Any]:
    """Read the prospect's website (nothing saved) and build the report."""
    from vula.commerce import brand_suggest, site_import
    st = await site_import.inspect_site(url)
    try:
        brand = await brand_suggest.suggest(website_url=st.get("url") or url)
    except Exception as exc:  # noqa: BLE001 — a brand we can't read never fails the scan
        log.debug("prospect brand skipped for %s: %s", url, exc)
        brand = {}
    return build_report(st, brand, socials or [], name)


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(row or {})
    if row.get("status") == "scanning":
        try:
            started = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - started > STALE_AFTER:
                row["status"], row["error"] = "failed", "The scan stopped (the server restarted). Scan again."
        except ValueError:
            pass
    return row


def create(url: str, name: str = "", socials: Any = None, created_by: str = "") -> Dict[str, Any]:
    from vula.commerce import site_import
    root = site_import.normalise_root(url)                 # ValueError for a non-address
    row = {"name": (name or "").strip()[:120] or urlparse(root).netloc, "url": root,
           "socials": clean_socials(socials), "status": "scanning", "report": None, "error": None,
           "created_by": created_by or None, "created_at": _now(), "updated_at": _now()}
    res = _client().table(TABLE).insert(row).execute()
    return (res.data or [row])[0]


async def run(prospect_id: str) -> None:
    """Scan one prospect and store the report (or why it failed). Never raises."""
    db = _client()
    rows = db.table(TABLE).select("*").eq("id", prospect_id).limit(1).execute().data or []
    if not rows:
        return
    p = rows[0]
    try:
        report = await scan(p["url"], p.get("socials") or [], p.get("name") or "")
        db.table(TABLE).update({"status": "done", "report": report, "error": None,
                                "updated_at": _now()}).eq("id", prospect_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("prospect scan failed for %s: %s", p.get("url"), exc)
        db.table(TABLE).update({"status": "failed", "error": str(exc)[:200],
                                "updated_at": _now()}).eq("id", prospect_id).execute()


_RUNNING: set = set()       # the scans in flight, held so a task isn't collected part-way


def start(prospect_id: str) -> None:
    """Scan in the background of this process; the row says "scanning" until it's done."""
    try:
        task = asyncio.get_running_loop().create_task(run(prospect_id))
    except RuntimeError:
        asyncio.run(run(prospect_id))
        return
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


def rescan(prospect_id: str) -> Optional[Dict[str, Any]]:
    res = (_client().table(TABLE).update({"status": "scanning", "error": None, "updated_at": _now()})
           .eq("id", prospect_id).execute())
    if not res.data:
        return None
    start(prospect_id)
    return _public(res.data[0])


def listing(limit: int = 100) -> List[Dict[str, Any]]:
    rows = (_client().table(TABLE).select("id,name,url,status,error,tenant_id,created_at,updated_at,report")
            .order("created_at", desc=True).limit(limit).execute().data or [])
    out = []
    for r in rows:
        rep = r.pop("report", None) or {}
        out.append({**_public(r), "kind": rep.get("kind"), "counts": rep.get("counts") or {}})
    return out


def get(prospect_id: str) -> Optional[Dict[str, Any]]:
    rows = _client().table(TABLE).select("*").eq("id", prospect_id).limit(1).execute().data or []
    return _public(rows[0]) if rows else None


def link_tenant(prospect_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    res = (_client().table(TABLE).update({"tenant_id": tenant_id, "updated_at": _now()})
           .eq("id", prospect_id).execute())
    return _public(res.data[0]) if res.data else None


def delete(prospect_id: str) -> bool:
    return bool(_client().table(TABLE).delete().eq("id", prospect_id).execute().data)
