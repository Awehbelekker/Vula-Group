"""
vula/api/menu_page.py — public, no-auth photo-menu page for a commerce tenant.

WhatsApp's interactive list message has no image slot per row (Meta platform limit), so this
page is the workaround: a simple photo grid of in-stock products, linked from the WhatsApp
welcome menu, using the image_url/description data already on commerce_products.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["menu_page"])

_PLACEHOLDER_IMG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='220'%3E"
    "%3Crect width='300' height='220' fill='%23DDD8CE'/%3E%3Ctext x='150' y='115' "
    "font-family='sans-serif' font-size='14' fill='%238A8680' text-anchor='middle'%3ENo photo yet"
    "%3C/text%3E%3C/svg%3E"
)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_price(cents) -> str:
    try:
        return f"R{int(cents) / 100:.2f}"
    except Exception:
        return ""


@router.get("/menu/{tenant_id}", response_class=HTMLResponse)
async def photo_menu(tenant_id: str) -> HTMLResponse:
    from vula.commerce import service as cs

    try:
        products = await cs.list_products(tenant_id, in_stock_only=True, statuses={"active"})
    except Exception:
        products = []
    try:
        inv = await cs.get_invoice_settings(tenant_id)
    except Exception:
        inv = None
    shop_name = _esc((inv or {}).get("trading_as") or (inv or {}).get("company_name") or tenant_id.replace("-", " ").title())
    # Brand-through (UI overhaul P4): the tenant's own accent + logo on their public menu.
    accent = (inv or {}).get("accent_color") or "#2C5545"
    if not accent.startswith("#"):
        accent = f"#{accent}"
    logo_url = _esc((inv or {}).get("logo_url") or "")

    by_cat: dict[str, list] = {}
    for p in products:
        by_cat.setdefault(p.get("category") or "Other", []).append(p)

    sections = []
    for cat, items in by_cat.items():
        label = _esc(cat.replace("_", " ").title())
        cards = []
        for p in items:
            img = _esc(p.get("image_url") or "") or _PLACEHOLDER_IMG
            name = _esc(p.get("name") or "Item")
            price = _fmt_price(p.get("price_cents") or 0)
            weight = f"{int(p['weight_grams'])}g" if p.get("weight_grams") else (
                f"serves {p['serves']}" if p.get("serves") else "")
            desc = _esc(p.get("description") or p.get("notes") or "")
            cards.append(f"""
                <div class="card">
                  <img src="{img}" loading="lazy" alt="{name}">
                  <div class="card-body">
                    <h3>{name}</h3>
                    <p class="meta">{price}{' · ' + weight if weight else ''}</p>
                    {f'<p class="desc">{desc}</p>' if desc else ''}
                  </div>
                </div>""")
        sections.append(f'<section><h2>{label}</h2><div class="grid">{"".join(cards)}</div></section>')

    body = "".join(sections) or "<p class='empty'>Nothing in stock right now — check back soon.</p>"

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{shop_name} — Menu</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #FAF8F3; color: #2A2A2A; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #1A1A18; color: #EDEAE2; }} }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #8A8680; font-size: 13px; margin: 0 0 20px; }}
  h2 {{ font-size: 16px; margin: 24px 0 10px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }}
  .card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  @media (prefers-color-scheme: dark) {{ .card {{ background: #2A2A26; }} }}
  .card img {{ width: 100%; height: 120px; object-fit: cover; display: block; }}
  .card-body {{ padding: 8px 10px; }}
  .card h3 {{ font-size: 13px; margin: 0 0 4px; }}
  .meta {{ font-size: 12px; color: {accent}; font-weight: 600; margin: 0 0 2px; }}
  .brandrow {{ display: flex; align-items: center; gap: 10px; }}
  .brandrow img {{ height: 40px; width: auto; object-fit: contain; }}
  .desc {{ font-size: 11px; color: #8A8680; margin: 0; }}
  .empty {{ color: #8A8680; }}
  .cta {{ margin-top: 28px; font-size: 13px; color: #8A8680; }}
</style></head>
<body>
  <div class="brandrow">
    {f'<img src="{logo_url}" alt="{shop_name}">' if logo_url else ''}
    <h1>{shop_name}</h1>
  </div>
  <p class="sub">Browse what's fresh — order on WhatsApp.</p>
  {body}
  <p class="cta">To order, message us on WhatsApp and reply <b>menu</b>.</p>
</body></html>"""
    return HTMLResponse(html)
