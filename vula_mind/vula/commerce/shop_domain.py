"""
vula/commerce/shop_domain.py — a shop's own web address, set up from the dashboard.

2026-10-13 (Ian: "can we streamline domain setup"): the business types shop.<their domain>
into Settings and Vula does the rest — adds it to this Railway service through Railway's
public API, saves it on the tenant, and shows the two DNS records to copy (one CNAME, one TXT)
with a live status: waiting for DNS → securing (certificate) → live. Links switch to it by
themselves once it answers (web_basket.shop_host checks /shop-ping), so nothing else changes.

Railway allows 20 custom domains per service on Pro (more on request) — past that, put the
shops behind one proxy instead (Cloudflare for SaaS).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import httpx

RAILWAY_API = "https://backboard.railway.com/graphql/v2"
PREFIXES = ("shop.", "order.", "buy.", "store.")
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class DomainError(Exception):
    pass


def normalise(raw: str) -> str:
    d = re.sub(r"^https?://", "", (raw or "").strip().lower()).split("/")[0].split(":")[0].rstrip(".")
    if not _DOMAIN_RE.match(d):
        raise DomainError("That doesn't look like a web address — e.g. shop.yourbusiness.co.za")
    if not d.startswith(PREFIXES):
        raise DomainError("Use an address that starts with shop. (or order., buy., store.) — "
                          "e.g. shop.yourbusiness.co.za, so your main website stays as it is.")
    if d.endswith((".railway.app", ".vercel.app", ".vula-ai.com")):
        raise DomainError("Use your own business's domain.")
    return d


def suggestion(tenant_id: str) -> str:
    """shop.<their store's domain>, when we know it."""
    try:
        from vula.api.tenants import get_config
        store = (get_config(tenant_id) or {}).get("store_url") or ""
    except Exception:
        store = ""
    host = re.sub(r"^https?://", "", store.lower()).split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return f"shop.{host}" if host and "." in host and not host.endswith((".myshopify.com", ".railway.app")) else ""


def _ids() -> Dict[str, str]:
    """This service's own Railway ids — Railway sets them in every deployment."""
    return {"projectId": os.environ.get("RAILWAY_PROJECT_ID", ""),
            "environmentId": os.environ.get("RAILWAY_ENVIRONMENT_ID", ""),
            "serviceId": os.environ.get("RAILWAY_SERVICE_ID", "")}


def can_automate() -> bool:
    from config import settings
    return bool(settings.railway_api_token and all(_ids().values()))


async def _gql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    from config import settings
    token = settings.railway_api_token
    if not token:
        raise DomainError("Custom addresses aren't switched on for Vula yet — ask Vula support.")
    async with httpx.AsyncClient(timeout=20.0) as client:
        for headers in ({"Project-Access-Token": token}, {"Authorization": f"Bearer {token}"}):
            resp = await client.post(RAILWAY_API, json={"query": query, "variables": variables}, headers=headers)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            errors = body.get("errors") or []
            if resp.status_code in (401, 403) or any("authoriz" in (e.get("message") or "").lower() for e in errors):
                continue                                  # a team token rather than a project one
            if errors:
                raise DomainError(errors[0].get("message") or "Railway refused that.")
            return body.get("data") or {}
    raise DomainError("Vula's Railway token was refused — ask Vula support to renew it.")


_LIST = """query domains($projectId: String!, $environmentId: String!, $serviceId: String!) {
  domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
    serviceDomains { domain targetPort }
    customDomains { id domain status { verificationToken certificateStatus
      dnsRecords { hostlabel requiredValue currentValue status } } } } }"""

_CREATE = """mutation customDomainCreate($input: CustomDomainCreateInput!) {
  customDomainCreate(input: $input) { id domain } }"""


async def _domains() -> Dict[str, Any]:
    return (await _gql(_LIST, _ids())).get("domains") or {}


async def _railway_domain(domain: str) -> Optional[Dict[str, Any]]:
    doms = await _domains()
    found = next((c for c in doms.get("customDomains") or [] if c.get("domain") == domain), None)
    if found:
        found["_target"] = next((s.get("domain") for s in doms.get("serviceDomains") or []), "")
    return found


def records(domain: str, rd: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The DNS records the business adds — name relative to their domain, type, value, status."""
    out = []
    st = (rd or {}).get("status") or {}
    zone = domain.split(".", 1)[1]
    for r in st.get("dnsRecords") or []:
        name = (r.get("hostlabel") or domain.split(".")[0]).replace(f".{zone}", "")
        out.append({"type": "CNAME", "name": name or "@", "value": r.get("requiredValue") or (rd or {}).get("_target") or "",
                    "ok": (r.get("status") or "").upper().endswith("VALID")
                    and not (r.get("status") or "").upper().endswith("INVALID")})
    if st.get("verificationToken"):
        out.append({"type": "TXT", "name": f"_railway-verify.{domain.split('.')[0]}",
                    "value": st["verificationToken"], "ok": None})
    return out


def _save(tenant_id: str, domain: Optional[str]) -> None:
    """Put the address on the tenant (or take it off) — one shop address at a time."""
    from vula.api import tenants
    from vula.commerce import service
    cfg = tenants.get_config(tenant_id, fresh=True) or {}
    doms = [d for d in (cfg.get("domains") or []) if not str(d).lower().startswith(PREFIXES)]
    if domain:
        doms.append(domain)
    service._client().table("vula_tenant_config").update({"domains": doms}).eq("tenant_id", tenant_id).execute()
    try:
        tenants.get_config(tenant_id, fresh=True)
    except Exception:
        pass


async def status(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import web_basket
    domain = web_basket.configured_shop_host(tenant_id)
    out: Dict[str, Any] = {"domain": domain, "suggestion": suggestion(tenant_id),
                           "automated": can_automate(), "state": "none", "records": []}
    if not domain:
        return out
    live = await web_basket.check_shop_host(domain, tenant_id)
    out["state"] = "live" if live else "waiting_dns"
    if not live and can_automate():
        try:
            rd = await _railway_domain(domain)
        except DomainError as exc:
            out["error"] = str(exc)
            rd = None
        out["records"] = records(domain, rd)
        cert = ((rd or {}).get("status") or {}).get("certificateStatus") or ""
        if out["records"] and all(r["ok"] for r in out["records"] if r["ok"] is not None) and "ISSU" not in cert:
            out["state"] = "securing"
    return out


async def connect(tenant_id: str, raw: str) -> Dict[str, Any]:
    domain = normalise(raw)
    from vula.api.tenants import tenant_for_host
    owner = tenant_for_host(domain)
    if owner and owner != tenant_id:
        raise DomainError("That address is already used by another business.")
    if can_automate():
        doms = await _domains()
        if not any(c.get("domain") == domain for c in doms.get("customDomains") or []):
            port = next((s.get("targetPort") for s in doms.get("serviceDomains") or [] if s.get("targetPort")), None)
            await _gql(_CREATE, {"input": {**_ids(), "domain": domain, **({"targetPort": port} if port else {})}})
    _save(tenant_id, domain)
    return await status(tenant_id)


async def disconnect(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import web_basket
    domain = web_basket.configured_shop_host(tenant_id)
    if domain and can_automate():
        rd = await _railway_domain(domain)
        if rd:
            await _gql("mutation d($id: String!) { customDomainDelete(id: $id) }", {"id": rd["id"]})
    _save(tenant_id, None)
    web_basket._READY.pop(domain, None)
    return await status(tenant_id)


def instructions(domain: str, recs: List[Dict[str, str]]) -> str:
    """For whoever looks after the business's domain — plain, copy-paste."""
    lines = [f"Please add these DNS records for {domain.split('.', 1)[1]}:", ""]
    for r in recs:
        lines.append(f"• {r['type']}  name: {r['name']}  value: {r['value']}")
    lines += ["", "If it's on Cloudflare, set the CNAME to “DNS only” (grey cloud).",
              "It usually works within an hour; Vula switches over by itself."]
    return "\n".join(lines)
