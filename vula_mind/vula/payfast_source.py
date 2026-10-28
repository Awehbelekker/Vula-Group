"""Is this PayFast ITN coming from PayFast? (defence in depth for /v1/payments/webhook/{t}/payfast)

An ITN is already trusted only after its signature checks out and PayFast's own server answers
VALID to the post-back (vula/tap/adapters.py). PayFast's integration guide adds a third check: the
notification must come from one of its servers. We take the caller's address from the right-most
X-Forwarded-For hop (the one Railway's edge appended, which the caller can't forge) and accept it
when it matches the documented ITN hosts (resolved, cached 10 min) or the configured ranges.

settings.payfast_itn_source_check:
  off      skip the check
  log      (default) log a mismatch, still process. Watch the logs after a real payment, then...
  enforce  ignore an ITN from anywhere else. A DNS failure fails open: the other two checks stand.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Iterable

log = logging.getLogger(__name__)

ITN_HOSTS = ("www.payfast.co.za", "sandbox.payfast.co.za", "w1w.payfast.co.za", "w2w.payfast.co.za")
_TTL = 600.0
_cache: dict = {"ips": frozenset(), "at": 0.0}


def caller_ip(headers: dict, client_host: str = "") -> str:
    """The address that connected to Railway's edge: the LAST X-Forwarded-For hop (earlier hops are
    whatever the caller sent). Falls back to the socket peer when there is no proxy header."""
    fwd = (headers.get("x-forwarded-for") or "").split(",")
    hop = fwd[-1].strip() if fwd and fwd[-1].strip() else ""
    return hop or (client_host or "")


def _resolve(hosts: Iterable[str]) -> frozenset:
    ips = set()
    for h in hosts:
        try:
            for info in socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP):
                ips.add(info[4][0])
        except OSError as exc:
            log.warning("PayFast host %s did not resolve: %s", h, exc)
    return frozenset(ips)


async def payfast_ips(now: float | None = None) -> frozenset:
    now = time.monotonic() if now is None else now
    if not _cache["ips"] or now - _cache["at"] > _TTL:
        ips = await asyncio.to_thread(_resolve, ITN_HOSTS)
        if ips:                                     # keep the last good set if DNS hiccups
            _cache.update(ips=ips, at=now)
    return _cache["ips"]


def in_ranges(ip: str, ranges: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for r in (ranges or "").split(","):
        r = r.strip()
        if not r:
            continue
        try:
            if addr in ipaddress.ip_network(r, strict=False):
                return True
        except ValueError:
            log.warning("bad PayFast ITN range in settings: %r", r)
    return False


async def allowed(headers: dict, client_host: str, *, mode: str, ranges: str) -> bool:
    """False only in enforce mode, for a caller that is provably not PayFast."""
    mode = (mode or "log").lower()
    if mode == "off":
        return True
    ip = caller_ip(headers, client_host)
    if ip and in_ranges(ip, ranges):
        return True
    resolved = await payfast_ips()
    if ip and ip in resolved:
        return True
    if mode != "enforce":
        log.warning("PayFast ITN from %s is not a known PayFast address (source check in log mode)", ip or "?")
        return True
    if not resolved and not (ranges or "").strip():
        log.warning("PayFast hosts unresolvable and no ranges set: source check skipped for %s", ip or "?")
        return True
    log.warning("PayFast ITN from %s ignored: not a PayFast address", ip or "?")
    return False
