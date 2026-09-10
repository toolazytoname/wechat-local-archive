"""Optional single-article fetch. Not used unless the user posts /fetch."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from wechat_export.insights.store import InsightsError
from wechat_export.link_urls import safe_web_url

BLOCKED_PORTS = {0, 22, 23, 25, 445, 3306, 3389, 5432, 6379, 11211, 27017}


def _blocked_host(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "metadata.google.internal"} or lowered.endswith(".localhost"):
        return True
    try:
        parsed = ipaddress.ip_address(host)
        return bool(
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        )
    except ValueError:
        return False


def validate_fetch_url(url: str, *, resolver=socket.getaddrinfo) -> str:
    safe = safe_web_url(url)
    if not safe:
        raise InsightsError("url not allowed", "fetch_denied")
    parts = urlsplit(safe)
    host = parts.hostname or ""
    if parts.username or parts.password:
        raise InsightsError("url not allowed", "fetch_denied")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port in BLOCKED_PORTS or port < 1:
        raise InsightsError("url not allowed", "fetch_denied")
    if _blocked_host(host):
        raise InsightsError("url not allowed", "fetch_denied")
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise InsightsError("url not allowed", "fetch_denied") from exc
    for item in answers:
        sockaddr = item[4]
        ip = sockaddr[0]
        if _blocked_host(ip):
            raise InsightsError("url not allowed", "fetch_denied")
    return safe


def fetch_not_enabled() -> None:
    """First version does not auto-fetch. Paste or local import instead."""
    raise InsightsError("external fetch is not enabled until you approve a single article", "fetch_disabled")
