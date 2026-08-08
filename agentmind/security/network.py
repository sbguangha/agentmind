"""Network security — SSRF protection for outgoing HTTP requests.

Migrated from nanobot's ``security/network.py`` (MIT): before the agent
fetches a URL we validate scheme + hostname + resolved IPs, refusing private
(SSRF) targets such as RFC1918, loopback, link-local and cloud metadata
addresses. Every redirect hop is re-validated before being followed.

``socket.getaddrinfo`` is blocking, so async callers use :func:`validate_url`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT / metadata
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
]

_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)


def _normalize_addr(addr):
    """Normalize IPv6-mapped IPv4 addresses (``::ffff:127.0.0.1`` -> ``127.0.0.1``)."""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_private(addr) -> bool:
    normalized = _normalize_addr(addr)
    return any(normalized in net for net in _BLOCKED_NETWORKS)


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def resolve_url_target(url: str, *, allow_loopback: bool = False) -> tuple[bool, str, tuple[str, ...]]:
    """Validate a URL: scheme, hostname, and resolved IPs (SSRF check).

    Returns ``(ok, error_message, resolved_ips)``.

    Blocking rule: a target is refused only when **every** resolved address is
    private/internal. Many real deployments resolve through a proxy that returns
    fake-DNS pairs (public IPv4 + a private ULA IPv6); blocking on *any* private
    address would break legitimate browsing. Pure loopback / RFC1918 / metadata
    literals still resolve to only private addresses and are always blocked.
    """
    try:
        p = urlparse(url)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), ()

    if p.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{p.scheme or 'none'}'", ()
    if not p.netloc:
        return False, "Missing domain", ()
    hostname = p.hostname
    if not hostname:
        return False, "Missing hostname", ()

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError, ValueError):
        return False, f"Cannot resolve hostname: {hostname}", ()

    addrs: list = []
    for info in infos:
        try:
            addrs.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue

    if not addrs:
        return False, f"Cannot resolve hostname: {hostname}", ()

    if allow_loopback and addrs and all(_normalize_addr(a).is_loopback for a in addrs) and _is_loopback_host(hostname):
        return True, "", tuple(dict.fromkeys(str(_normalize_addr(a)) for a in addrs))

    if all(_is_private(a) for a in addrs):
        return False, f"Blocked: {hostname} resolves only to private/internal addresses", ()

    return True, "", tuple(dict.fromkeys(str(_normalize_addr(a)) for a in addrs))


async def validate_url(url: str, *, allow_loopback: bool = False) -> tuple[bool, str]:
    """Async SSRF validation (DNS resolution runs in a thread)."""
    ok, error, _ = await asyncio.to_thread(
        resolve_url_target, url, allow_loopback=allow_loopback
    )
    return ok, error


def contains_internal_url(text: str, *, allow_loopback: bool = False) -> bool:
    """Return True when *text* contains a URL pointing at an internal address."""
    for match in _URL_RE.finditer(text):
        ok, _, _ = resolve_url_target(match.group(0), allow_loopback=allow_loopback)
        if not ok:
            return True
    return False
