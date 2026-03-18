"""
SSRF (Server-Side Request Forgery) protection for outbound HTTP requests.

Two-phase validation inspired by OpenClaw's ssrf.ts:
  Phase 1: Check the hostname/IP directly (fast fail for obvious private IPs).
  Phase 2: Resolve DNS and verify all returned IPs are public (prevents DNS
           rebinding attacks where evil.com resolves to 127.0.0.1).

Blocked targets:
  - localhost / 127.0.0.0/8
  - Private networks: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
  - Link-local: 169.254.0.0/16
  - Metadata servers: 169.254.169.254 (AWS/GCP)
  - Docker socket paths
  - .local / .internal hostnames
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse
from typing import Optional, Union

logger = logging.getLogger("winston.security.ssrf_guard")

# Hostnames that are always blocked
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.google.com",
    "instance-data",
}

# Hostname suffixes that are blocked
_BLOCKED_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".localdomain",
)

# Private / special-use IPv4 networks
_BLOCKED_NETWORKS_V4 = [
    ipaddress.IPv4Network("0.0.0.0/8"),         # "This" network
    ipaddress.IPv4Network("10.0.0.0/8"),         # RFC1918 private
    ipaddress.IPv4Network("127.0.0.0/8"),        # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),     # Link-local (APIPA)
    ipaddress.IPv4Network("172.16.0.0/12"),      # RFC1918 private
    ipaddress.IPv4Network("192.168.0.0/16"),     # RFC1918 private
    ipaddress.IPv4Network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.IPv4Network("192.0.2.0/24"),       # Documentation (TEST-NET-1)
    ipaddress.IPv4Network("198.18.0.0/15"),      # Benchmarking
    ipaddress.IPv4Network("198.51.100.0/24"),    # Documentation (TEST-NET-2)
    ipaddress.IPv4Network("203.0.113.0/24"),     # Documentation (TEST-NET-3)
    ipaddress.IPv4Network("224.0.0.0/4"),        # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),        # Reserved
    ipaddress.IPv4Network("255.255.255.255/32"), # Broadcast
]

# Private / special-use IPv6 networks
_BLOCKED_NETWORKS_V6 = [
    ipaddress.IPv6Network("::1/128"),            # Loopback
    ipaddress.IPv6Network("::/128"),             # Unspecified
    ipaddress.IPv6Network("fc00::/7"),           # Unique local
    ipaddress.IPv6Network("fe80::/10"),          # Link-local
    ipaddress.IPv6Network("ff00::/8"),           # Multicast
    ipaddress.IPv6Network("::ffff:0:0/96"),      # IPv4-mapped (check v4 part too)
]


class SSRFError(Exception):
    """Raised when a URL fails SSRF validation."""
    pass


def validate_url(url: str) -> str:
    """
    Validate a URL against SSRF attacks.  Returns the validated URL on success.

    Raises SSRFError if the URL targets a private/blocked network.
    """
    parsed = urlparse(url)

    # Only allow http(s)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"Blocked scheme '{parsed.scheme}' — only http/https allowed")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError(f"No hostname in URL: {url}")

    # ── Phase 1: hostname-level checks (fast) ────────────────────

    hostname_lower = hostname.lower().rstrip(".")

    # Blocked exact hostnames
    if hostname_lower in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Blocked hostname: {hostname}")

    # Blocked suffixes
    if any(hostname_lower.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise SSRFError(f"Blocked hostname suffix: {hostname}")

    # If hostname is a literal IP, check it directly
    try:
        ip = ipaddress.ip_address(hostname)
        _check_ip(ip)
        return url
    except ValueError:
        pass  # Not a literal IP — proceed to DNS resolution

    # ── Phase 2: DNS resolution check (prevents rebinding) ───────

    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {hostname}: {e}")

    if not addrinfos:
        raise SSRFError(f"No DNS results for {hostname}")

    for family, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            _check_ip(ip)
        except SSRFError:
            raise SSRFError(
                f"DNS for {hostname} resolved to blocked address {ip_str}"
            )

    return url


def _check_ip(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> None:
    """Raise SSRFError if *ip* is in a blocked network."""
    if isinstance(ip, ipaddress.IPv4Address):
        for network in _BLOCKED_NETWORKS_V4:
            if ip in network:
                raise SSRFError(f"Blocked private/reserved IPv4: {ip}")
    elif isinstance(ip, ipaddress.IPv6Address):
        for network in _BLOCKED_NETWORKS_V6:
            if ip in network:
                raise SSRFError(f"Blocked private/reserved IPv6: {ip}")

        # Also check IPv4-mapped IPv6 addresses (::ffff:192.168.1.1)
        if ip.ipv4_mapped:
            _check_ip(ip.ipv4_mapped)
