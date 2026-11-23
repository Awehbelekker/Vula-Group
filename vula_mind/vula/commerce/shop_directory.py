"""The shop directory (24 Nov 2026): one public page listing the shops that asked to be on it.

Every shop has its own address (`web_basket.free_shop_host`); nothing yet let a customer find one
they didn't already know. The directory answers on `directory.<shop_base_domain>` (and `www.` and
the bare base domain, once those point here) — under the same wildcard domain, so nothing to set up.

A shop is on it only when ALL of these hold, read at the time the page is built:
  - its owner or a manager chose to be listed (`vula_tenant_config.features.directory.listed`) —
    never by default, never by Vula on its behalf;
  - the business is active (not suspended);
  - it has something to sell (a live, in-stock product);
  - its shop address answers (`web_basket.shop_host`), so no card links to a page that isn't up.

What a card shows comes from the business's own records — its trading name, logo, colour, a line it
wrote itself, its town, a product photo. Nothing is generated. The line and the town are plain text
(no links, one line), escaped on the way out.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

KEY = "directory"                       # inside vula_tenant_config.features
LABEL = "directory"
ABOUT_MAX, TOWN_MAX = 160, 40
CACHE_S = 300.0
_CACHE: Dict[str, Any] = {"at": 0.0, "rows": None}

# business type → the heading a shopper would look under
CATEGORIES = {"food": "Food & drink", "retail": "Shops", "health": "Health & wellness",
              "services": "Services", "trades": "Trades", "real_estate": "Property"}
OTHER = "Other"

_LINK_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:co\.za|com|net|org|africa|shop)\b", re.I)


class DirectoryError(ValueError):
    """A listing the business asked for that can't be saved as asked — the message is for them."""


# ── where it lives ────────────────────────────────────────────────────────────────────────────
def host() -> str:
    """directory.shop.vula-ai.com — '' when free shop addresses aren't set up."""
    from vula.commerce import web_basket
    base = web_basket.shop_base_domain()
    return f"{LABEL}.{base}" if base else ""


def is_directory_host(h: str) -> bool:
    from vula.commerce import web_basket
    base = web_basket.shop_base_domain()
    h = (h or "").strip().lower().split(":")[0]
    return bool(base) and h in (base, f"www.{base}", f"{LABEL}.{base}")


def url() -> str:
    h = host()
    return f"https://{h}/" if h else ""


# ── a business's own listing ──────────────────────────────────────────────────────────────────
def _one_line(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit].strip()


def clean(about: Any, town: Any) -> Dict[str, str]:
    """The business's line and town as they will be shown. DirectoryError when the line carries a
    web address — the card already links to the shop, and a line of links is an advert for
    somewhere else."""
    a, t = _one_line(about, ABOUT_MAX), _one_line(town, TOWN_MAX)
    if _LINK_RE.search(a) or _LINK_RE.search(t):
        raise DirectoryError("Leave web addresses out — the listing already links to your shop.")
    return {"about": a, "town": t}


def stored(tenant_id: str, fresh: bool = False) -> Dict[str, Any]:
    from vula.api.tenants import get_config
    try:
        f = (get_config(tenant_id, fresh=fresh) or {}).get("features")
    except Exception:  # noqa: BLE001 — unreadable means not listed
        f = None
    d = f.get(KEY) if isinstance(f, dict) else None
    d = d if isinstance(d, dict) else {}
    return {"listed": d.get("listed") is True, "about": _one_line(d.get("about"), ABOUT_MAX),
            "town": _one_line(d.get("town"), TOWN_MAX)}


async def status(tenant_id: str) -> Dict[str, Any]:
    """What the business sees in Settings: its listing, whether it is showing, and if not, why."""
    from vula.api import tenants
    from vula.commerce import shop_domain, web_basket
    out: Dict[str, Any] = {**stored(tenant_id), "available": bool(host()), "directory_url": url(),
                           "about_max": ABOUT_MAX, "town_max": TOWN_MAX, "showing": False, "why_not": ""}
    if not out["available"]:
        out["why_not"] = "The directory isn't switched on yet."
        return out
    try:
        has_products = bool(await web_basket.shop_products(tenant_id))
    except Exception:  # noqa: BLE001
        has_products = False
    shop = web_basket.shop_host(tenant_id)
    if not shop:
        free = await shop_domain.free_address(tenant_id)
        shop = free["host"] if free and free.get("live") else ""
    out["shop_url"] = f"https://{shop}/" if shop else ""
    if not out["listed"]:
        out["why_not"] = "Your shop isn't listed."
    elif not tenants.is_active(tenant_id):
        out["why_not"] = "This business is paused."
    elif not has_products:
        out["why_not"] = "Your shop has no products in stock, so there is nothing to show yet."
    elif not shop:
        out["why_not"] = "Your shop's address isn't answering yet. It will show once it does."
    else:
        out["showing"] = True
    return out


async def save(tenant_id: str, *, listed: Any, about: Any = "", town: Any = "",
               actor: Optional[dict] = None) -> Dict[str, Any]:
    """List or unlist the shop, with its line and town. Written inside the business's own switches
    (other keys untouched), read back, on the business's record."""
    if not isinstance(listed, bool):
        raise DirectoryError("Say whether to list your shop: listed must be true or false.")
    want = {"listed": listed, **clean(about, town)}
    from vula.api import tenants
    from vula.commerce import service
    db = service._client()
    rows = (db.table("vula_tenant_config").select("features").eq("tenant_id", tenant_id).limit(1)
            .execute().data or [])
    if not rows:
        raise DirectoryError("This business has no settings row yet.")
    feats = rows[0].get("features") if isinstance(rows[0].get("features"), dict) else {}
    db.table("vula_tenant_config").update({"features": {**feats, KEY: want}}).eq("tenant_id", tenant_id).execute()
    tenants.invalidate(tenant_id)
    back = stored(tenant_id, fresh=True)
    if back != want:
        raise DirectoryError("The listing didn't save. Nothing changed.")
    _CACHE["rows"] = None
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, "shop_directory_listing", **want)
    except Exception as exc:  # noqa: BLE001
        log.error("directory audit failed for %s: %s", tenant_id, exc)
    return await status(tenant_id)


# ── the listed shops ──────────────────────────────────────────────────────────────────────────
def _tenant_rows() -> List[dict]:
    from vula.commerce import service
    from vula.db_pages import all_rows
    return all_rows(lambda: service._client().table("vula_tenant_config")
                    .select("tenant_id,display_name,business_type,active,features"), order="tenant_id")


def _accent(raw: Any) -> str:
    a = str(raw or "").strip()
    a = a if a.startswith("#") else f"#{a}"
    return a if re.fullmatch(r"#[0-9a-fA-F]{6}", a) else "#2C5545"


async def _entry(row: dict) -> Optional[Dict[str, Any]]:
    from vula.commerce import service, web_basket
    tid = row.get("tenant_id") or ""
    feats = row.get("features") if isinstance(row.get("features"), dict) else {}
    d = feats.get(KEY) if isinstance(feats.get(KEY), dict) else {}
    if not tid or d.get("listed") is not True or row.get("active") is False:
        return None
    products = await web_basket.shop_products(tid)
    shop = web_basket.shop_host(tid)
    if not products or not shop:
        return None
    try:
        inv = await service.get_invoice_settings(tid) or {}
    except Exception:  # noqa: BLE001
        inv = {}
    name = (inv.get("trading_as") or inv.get("company_name") or row.get("display_name")
            or tid.replace("-", " ").replace("_", " ").title())
    image = next((web_basket.sized(p.get("image_url"), 600) for p in products if p.get("image_url")), "") or ""
    return {"tenant": tid, "name": _one_line(name, 80), "url": f"https://{shop}/",
            "about": _one_line(d.get("about"), ABOUT_MAX), "town": _one_line(d.get("town"), TOWN_MAX),
            "category": CATEGORIES.get(row.get("business_type") or "", OTHER),
            "logo": str(inv.get("logo_url") or ""), "accent": _accent(inv.get("accent_color")),
            "image": image, "products": len(products)}


async def entries(fresh: bool = False) -> List[Dict[str, Any]]:
    """The shops on the directory right now, by name. Kept five minutes; a listing change clears it."""
    if not fresh and _CACHE["rows"] is not None and time.monotonic() - _CACHE["at"] < CACHE_S:
        return _CACHE["rows"]
    out: List[Dict[str, Any]] = []
    for row in _tenant_rows():
        try:
            e = await _entry(row)
        except Exception as exc:  # noqa: BLE001 — one unreadable shop never empties the page
            log.warning("directory: %s left out: %s", row.get("tenant_id"), exc)
            e = None
        if e:
            out.append(e)
    out.sort(key=lambda e: (e["name"].lower(), e["tenant"]))
    _CACHE.update(at=time.monotonic(), rows=out)
    return out


# ── the page ──────────────────────────────────────────────────────────────────────────────────
def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _http(u: str) -> str:
    return u if str(u or "").startswith("https://") else ""


def _card(e: Dict[str, Any]) -> str:
    img = _http(e["image"])
    logo = _http(e["logo"])
    meta = " · ".join(x for x in (e["category"], e["town"]) if x)
    pic = (f'<img class="ph" src="{_esc(img)}" alt="" loading="lazy">' if img
           else f'<div class="ph none" style="background:{_esc(e["accent"])}"></div>')
    mark = (f'<img class="lg" src="{_esc(logo)}" alt="" loading="lazy">' if logo
            else f'<span class="lg ini" style="background:{_esc(e["accent"])}">{_esc(e["name"][:1].upper())}</span>')
    search = _esc(" ".join((e["name"], e["about"], e["town"], e["category"])).lower())
    return (f'<li class="card" data-cat="{_esc(e["category"])}" data-q="{search}">'
            f'<a href="{_esc(e["url"])}">{pic}<div class="bd">{mark}<div class="tx">'
            f'<h2>{_esc(e["name"])}</h2><p class="mt">{_esc(meta)}</p>'
            + (f'<p class="ab">{_esc(e["about"])}</p>' if e["about"] else "")
            + '</div></div></a></li>')


def page(rows: List[Dict[str, Any]], page_url: str) -> str:
    from vula.commerce import powered_by
    cats: List[str] = []
    for e in rows:
        if e["category"] not in cats:
            cats.append(e["category"])
    cats.sort(key=lambda c: (c == OTHER, c))
    chips = "".join(f'<button type="button" class="chip" data-cat="{_esc(c)}" aria-pressed="false">{_esc(c)}</button>'
                    for c in cats) if len(cats) > 1 else ""
    ld = json.dumps({"@context": "https://schema.org", "@type": "ItemList", "name": "Shops on Vula",
                     "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": e["url"],
                                          "name": e["name"]} for i, e in enumerate(rows[:500])]}
                    ).replace("<", "\\u003c")
    desc = "Local South African shops you can order from online and pay on WhatsApp."
    cards = "".join(_card(e) for e in rows)
    body = (f'<ul class="grid" id="grid">{cards}</ul><p class="empty" id="none" hidden>No shop matches that.</p>'
            if rows else '<p class="empty">No shops are listed yet.</p>')
    tools = ('<div class="tools"><input id="q" type="search" placeholder="Search shops, towns, what they sell" '
             f'aria-label="Search shops" autocomplete="off"><div class="chips">{chips}</div></div>') if rows else ""
    head = (f'<meta name="description" content="{_esc(desc)}">'
            f'<link rel="canonical" href="{_esc(page_url)}">'
            f'<meta property="og:title" content="Shops on Vula"><meta property="og:type" content="website">'
            f'<meta property="og:description" content="{_esc(desc)}"><meta property="og:url" content="{_esc(page_url)}">'
            f'<script type="application/ld+json">{ld}</script>')
    # The fixed parts first, then what came from the businesses — with "{{" broken up, so a shop
    # name can never stand in for one of the page's own slots.
    return (_PAGE.replace("{{PB_CSS}}", powered_by.pill_css()).replace("{{PB}}", powered_by.pill_html())
            .replace("{{COUNT}}", f"{len(rows)} shop{'' if len(rows) == 1 else 's'}" if rows else "")
            .replace("{{TOOLS}}", tools)
            .replace("{{HEAD}}", head.replace("{{", "{ {"))
            .replace("{{BODY}}", body.replace("{{", "{ {")))


def sitemap(rows: List[Dict[str, Any]], page_url: str) -> str:
    locs = [page_url] + [e["url"] for e in rows]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"  <url><loc>{_esc(u)}</loc></url>\n" for u in locs) + "</urlset>")


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shops on Vula</title>
{{HEAD}}
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#17201c;--mute:#5d6964;--line:#e3e1db;--accent:#2C5545}
@media (prefers-color-scheme:dark){:root{--bg:#111614;--card:#1a211e;--ink:#eef2ef;--mute:#9aa7a1;--line:#2a332f;--accent:#7fc4a6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 16px 8px}
h1{font-size:30px;line-height:1.15;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--mute);margin:0 0 20px}
.tools{display:flex;flex-direction:column;gap:12px;margin:0 0 20px}
#q{width:100%;padding:12px 14px;font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px}
#q:focus-visible,.chip:focus-visible,.card a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font:inherit;font-size:14px;padding:6px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:inherit;cursor:pointer}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.grid{list-style:none;margin:0;padding:0;display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.card a{display:block;height:100%;background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;color:inherit;text-decoration:none}
.card a:hover{border-color:var(--accent)}
.ph{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--line)}
.ph.none{opacity:.85}
.bd{display:flex;gap:12px;padding:14px}
.lg{flex:none;width:44px;height:44px;border-radius:10px;object-fit:contain;background:#fff;border:1px solid var(--line)}
.lg.ini{display:grid;place-items:center;color:#fff;font-weight:700;border:0}
.tx{min-width:0}
h2{font-size:17px;line-height:1.25;margin:0;overflow-wrap:anywhere}
.mt{margin:2px 0 0;font-size:13px;color:var(--mute)}
.ab{margin:8px 0 0;font-size:14px;overflow-wrap:anywhere}
.empty{color:var(--mute);padding:40px 0;text-align:center}
[hidden]{display:none!important}
{{PB_CSS}}
</style></head><body><main>
<h1>Shops on Vula</h1>
<p class="sub">Order online from local businesses and pay on WhatsApp. {{COUNT}}</p>
{{TOOLS}}
{{BODY}}
</main>{{PB}}
<script>
(function(){var q=document.getElementById('q'),g=document.getElementById('grid');if(!q||!g)return;
var none=document.getElementById('none'),cat='',cards=[].slice.call(g.children),chips=[].slice.call(document.querySelectorAll('.chip'));
function run(){var t=q.value.trim().toLowerCase(),n=0;cards.forEach(function(c){
var ok=(!cat||c.getAttribute('data-cat')===cat)&&(!t||c.getAttribute('data-q').indexOf(t)>-1);c.hidden=!ok;if(ok)n++;});
none.hidden=n>0;}
q.addEventListener('input',run);
chips.forEach(function(b){b.addEventListener('click',function(){var c=b.getAttribute('data-cat');cat=cat===c?'':c;
chips.forEach(function(x){x.setAttribute('aria-pressed',String(x.getAttribute('data-cat')===cat));});run();});});
})();
</script></body></html>"""
