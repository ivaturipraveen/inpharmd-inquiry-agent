"""Shared SSRF protection for every outbound fetch of a client-/platform-
supplied URL (excel_service.py, attachment_extraction_service.py,
excel_writeback_service.py).

Why resolve-then-classify, never classify-then-resolve: `socket.getaddrinfo`
accepts obfuscated numeric-IP hostnames (decimal `2130706433`, hex
`0x7f000001`, octal `0177.0.0.1`, short-form `127.1`) and resolves them to
real loopback/private addresses, while `ipaddress.ip_address()` rejects all
of those raw string forms outright. Validating the raw hostname string with
`ipaddress` would therefore miss every one of these bypasses — this module
always resolves first via `getaddrinfo`, then classifies the resolved
address, never the input string.

Why pin the connection: a resolve-then-connect-by-hostname flow still lets a
DNS record change between the validation lookup and the actual TCP connect
(DNS rebinding / TOCTOU). `safe_get` eliminates that gap by rewriting the
request URL's host to a specific validated IP literal, sending an explicit
Host header and `sni_hostname` extension so TLS SNI/certificate verification
still happens against the original hostname, and manually re-validating every
redirect hop (httpx redirects are disabled so this module controls them).
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

log = logging.getLogger("inquiry.ssrf_protection")

_ALLOWED_SCHEMES = ("http", "https")
_MAX_REDIRECTS = 5

# RFC 6598 Carrier-Grade NAT / Shared Address Space — not globally routable,
# but ipaddress.IPv4Address.is_private does not flag it.
_CGNAT_RANGE = ipaddress.ip_network("100.64.0.0/10")


class BlockedUrlError(Exception):
    """Raised when a URL fails SSRF validation — scheme, destination
    address, or redirect target is not permitted."""


def _is_public_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
        or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_RANGE)
    ):
        return False
    return True


def _resolve_all(hostname: str) -> list[str]:
    """All resolved IP literal strings for hostname, via the OS resolver —
    never via ipaddress.ip_address() on the raw hostname (see module
    docstring for why)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise BlockedUrlError(f"could not resolve host: {e}") from e
    return sorted({info[4][0] for info in infos})


def _validate_hostname(hostname: str) -> str:
    """Resolve hostname and require every resolved address to be public.
    Returns the first resolved address, to connect to directly (pinning)."""
    if not hostname:
        raise BlockedUrlError("missing hostname")
    addresses = _resolve_all(hostname)
    if not addresses:
        raise BlockedUrlError("host resolved to no addresses")
    for raw in addresses:
        addr = ipaddress.ip_address(raw)
        if not _is_public_address(addr):
            raise BlockedUrlError(f"host resolves to a non-public address ({raw})")
    return addresses[0]


def _validate_url(url: str) -> tuple[str, str]:
    """Validate scheme + destination for url. Returns (hostname, pinned_ip)."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"disallowed scheme: {parts.scheme!r}")
    hostname = parts.hostname or ""
    pinned_ip = _validate_hostname(hostname)
    return hostname, pinned_ip


def _pin_url_to_ip(url: str, hostname: str, pinned_ip: str) -> str:
    """Rewrite url's host to the literal pinned_ip, preserving port/path/query."""
    parts = urlsplit(url)
    literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = literal if parts.port is None else f"{literal}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def safe_get(
    client: httpx.Client,
    url: str,
    *,
    headers: Optional[dict] = None,
) -> httpx.Response:
    """GET url through client, enforcing SSRF protection on the initial URL
    and on every redirect hop. The connection is pinned to the exact
    validated IP (DNS-rebinding/TOCTOU-safe) while TLS SNI/certificate
    verification still targets the original hostname via the `sni_hostname`
    request extension. Redirects are followed manually (client-level
    `follow_redirects` must be False) up to `_MAX_REDIRECTS` hops, each
    independently re-validated.
    """
    next_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        hostname, pinned_ip = _validate_url(next_url)
        pinned_url = _pin_url_to_ip(next_url, hostname, pinned_ip)
        request = client.build_request(
            "GET",
            pinned_url,
            headers={**(headers or {}), "Host": hostname},
            extensions={"sni_hostname": hostname},
        )
        response = client.send(request, follow_redirects=False)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise BlockedUrlError("redirect response missing Location header")
            next_url = urljoin(next_url, location)
            continue
        return response
    raise BlockedUrlError("too many redirects")
