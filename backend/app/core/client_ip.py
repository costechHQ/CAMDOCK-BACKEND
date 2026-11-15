"""The address a request really came from.

`X-Forwarded-For` is a header any client can set, so it is believed only when the
direct connection comes from a configured trusted proxy.
"""

import ipaddress
from collections.abc import Iterable

from fastapi import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(value: str) -> tuple[Network, ...]:
    """Comma-separated addresses or CIDR ranges; raises ValueError when one is bad."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return tuple(ipaddress.ip_network(part, strict=False) for part in parts)


def _trusted(address: str, networks: Iterable[Network]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_ip(request: Request, trusted_proxies: tuple[Network, ...] = ()) -> str:
    direct = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not trusted_proxies or not forwarded or not _trusted(direct, trusted_proxies):
        return direct
    hops = [hop.strip() for hop in forwarded.split(",")]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return direct  # A malformed header is not believed at all.
        if not _trusted(hop, trusted_proxies):
            return hop
    return hops[0]
