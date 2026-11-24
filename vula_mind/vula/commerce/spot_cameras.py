"""Live cameras at surf spots (24 Nov 2026) — one list, kept by the partner, not by each school.

A camera belongs to whoever put it up (a lifesaving club, a surf shop, a webcam site). Being free
to watch on its owner's page is not leave to show it inside another app, so each camera has one of
two states:

  link   — the app shows a button that opens the OWNER'S page. Always allowed: it is a link.
  embed  — the owner said yes (`permitted_on` is the date they did), so the app may play the
           owner's own player inside it. Only players on `EMBED_HOSTS` are ever embedded.

A camera with an `embed_url` but no `permitted_on` is still a link: the address is kept so that
turning it on later is one line, not a search. A school's customers see the cameras within
`NEAR_KM` of the school's spot; the camera's own name and owner are shown, the school's position
is not. Adding a camera: check its page is live, add it as a link, ask its owner about embedding.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NEAR_KM = 6.0
# the only places a player may be loaded from, whatever a list entry says
EMBED_HOSTS = ("ipcamlive.com", "www.youtube-nocookie.com")

CAMERAS: List[Dict[str, Any]] = [
    # Read on 24 Nov 2026: the club's home page carries an IPCamLive player (alias bigbaybigboy)
    # and no word on re-use. A safety project by a club member (Tygerburger, Feb 2024). Asked for
    # permission to embed — until the club says yes this is a link to their page.
    {"id": "big-bay-slsc", "name": "Big Bay beach camera", "spot": "Big Bay", "lat": -33.7946, "lng": 18.4577,
     "owner": "Big Bay Surf Lifesaving Club", "page_url": "https://bigbaylifesaving.co.za/",
     "embed_url": "https://g0.ipcamlive.com/player/player.php?alias=bigbaybigboy&autoplay=0", "permitted_on": None},
]


def _embeddable(cam: Dict[str, Any]) -> Optional[str]:
    """The player's address when the owner has agreed AND it is on a host we embed; else None."""
    url = cam.get("embed_url") or ""
    host = (urlparse(url).hostname or "").lower()
    ok = url.startswith("https://") and any(host == h or host.endswith("." + h) for h in EMBED_HOSTS)
    return url if cam.get("permitted_on") and ok else None


def public(cam: Dict[str, Any]) -> Dict[str, Any]:
    """A camera as the app shows it: its name, whose it is, the owner's page, and the player's
    address only where the owner agreed."""
    embed = _embeddable(cam)
    return {"id": cam["id"], "name": cam["name"], "spot": cam["spot"], "owner": cam["owner"],
            "page_url": cam["page_url"], "kind": "embed" if embed else "link", "embed_url": embed}


def near(lat: Any, lng: Any, cameras: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The cameras within NEAR_KM of a position, nearest first. None for a position that isn't one."""
    from vula.commerce.lesson_directory import distance_km
    try:
        la, lo = float(lat), float(lng)
    except (TypeError, ValueError):
        return []
    found = [(distance_km(la, lo, c["lat"], c["lng"]), c) for c in (CAMERAS if cameras is None else cameras)]
    return [public(c) for d, c in sorted(found, key=lambda x: x[0]) if d <= NEAR_KM]
