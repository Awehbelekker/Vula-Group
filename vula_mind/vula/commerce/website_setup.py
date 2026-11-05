"""
vula/commerce/website_setup.py — set a business up from its website, in one go (5 Nov 2026,
Ian: "what happened to the brand kit finder to pull … details from their website" — the old
Home's "Make it yours" card went with the old design).

One address:
- the brand — colours (and the logo, when the site has one) from brand_suggest.py, saved straight
  away while the business still wears Vula's default look (no logo, Vula green). A business that
  has chosen its brand keeps it; the suggestion is returned for it to apply if it wants.
- "Import from your website" (site_import.py) in the background: products, the pages customers
  ask about into the knowledge base, and the business-profile answers drafted from those pages
  (profile_from_site.py) for the owner to confirm on WhatsApp.

Used by Master's "+ New tenant" (a Website field) and the Home card "Make it yours".
"""
from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger(__name__)

VULA_GREEN = "#2C5545"


def is_default_brand(settings: Dict[str, Any]) -> bool:
    accent = (settings.get("accent_color") or "").strip().upper()
    return not settings.get("logo_url") and accent in ("", VULA_GREEN)


async def start(tenant_id: str, url: str) -> Dict[str, Any]:
    """Brand now, import in the background. Raises ValueError for an address that can't be used."""
    from vula.commerce import brand_suggest, service, site_import
    from vula.commerce.background_tasks import run_background
    root = site_import.normalise_root(url)

    brand: Dict[str, Any] = {"applied": False}
    try:
        current = await service.get_invoice_settings(tenant_id) or {}
        suggestion = await brand_suggest.suggest(website_url=root)
        if suggestion and is_default_brand(current):
            patch = {k: suggestion[k] for k in ("accent_color", "secondary_color", "ink_color", "logo_url")
                     if suggestion.get(k)}
            await service.upsert_invoice_settings(tenant_id, patch)
            brand = {"applied": True, **patch, "why": suggestion.get("why")}
        elif suggestion:
            brand = {"applied": False, "suggestion": suggestion, "reason": "brand_already_set"}
        else:
            brand = {"applied": False, "reason": "no_colours_found"}
    except Exception as exc:
        log.info("website setup: brand step skipped for %s: %s", tenant_id, exc)
        brand = {"applied": False, "reason": "unreadable"}

    started = False
    if not site_import.status(tenant_id).get("running"):
        try:
            from vula.api.tenants import get_config
            business = (get_config(tenant_id) or {}).get("display_name") or ""
        except Exception:
            business = ""
        run_background(tenant_id, "site_import", site_import.import_site(tenant_id, root, business))
        started = True
    return {"url": root, "brand": brand, "import_started": started}
