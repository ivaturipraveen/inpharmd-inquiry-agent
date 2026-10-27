"""Shared SSRF protection and trust/logging helpers for every outbound fetch
of a client-/platform-supplied URL (excel_service.py,
attachment_extraction_service.py, excel_writeback_service.py). `fetch()` is
the single entry point those three modules call.

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

import inpharmd_service

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


def safe_url_for_log(url: Optional[str]) -> str:
    """scheme://host[:port]/path only — strips userinfo, query string, and
    fragment so a presigned URL's signature (or embedded credentials) never
    reaches logs or error responses."""
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}{parsed.path}"
    except Exception:
        return "(unparseable url)"


def _trusted_inpharmd_host() -> str:
    """The exact host configured via INPHARMD_API_BASE_URL — empty if unset
    or malformed, which makes is_trusted_inpharmd_url fail closed (never
    appends the token anywhere) rather than falling back to a guessed host."""
    try:
        base = inpharmd_service._base_url()
    except inpharmd_service.InpharmdConfigError:
        return ""
    return (urlsplit(base).hostname or "").lower()


def is_trusted_inpharmd_url(url: str) -> bool:
    """True only for the exact host configured via INPHARMD_API_BASE_URL (or
    one of its subdomains) — never a substring match, so a lookalike host
    like `my-inpharmd-mirror.evil.com` can never receive the access token.
    S3 buckets are naturally excluded since they never match that host."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    trusted = _trusted_inpharmd_host()
    if not host or not trusted:
        return False
    return host == trusted or host.endswith("." + trusted)


def fetch(url: str, *, access_token: Optional[str] = None, timeout: float) -> bytes:
    """Shared download path for every client-/platform-supplied URL this app
    fetches. Appends access_token as a query param only when url is the
    trusted InpharmD host (never to S3/R2/any other host), then performs an
    SSRF-protected GET via safe_get and returns the response body.

    This is the single implementation behind excel_service.download_excel,
    attachment_extraction_service._download, and
    excel_writeback_service._download — previously each had its own
    copy of this token-gating logic."""
    final_url = url
    if access_token and is_trusted_inpharmd_url(url) and "access_token=" not in url:
        sep = "&" if "?" in url else "?"
        final_url = f"{url}{sep}access_token={access_token}"
    with httpx.Client(timeout=timeout) as client:
        res = safe_get(client, final_url)
        res.raise_for_status()
        return res.content
