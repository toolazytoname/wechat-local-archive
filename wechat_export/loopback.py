"""Bind-address policy for the archive server."""

from __future__ import annotations

LOOPBACK_HOST = "127.0.0.1"


class BindAddressError(ValueError):
    pass


def validate_bind_host(host: str) -> str:
    if host != LOOPBACK_HOST:
        raise BindAddressError(f"refusing non-loopback bind {host!r}; only {LOOPBACK_HOST} is allowed")
    return host


def allowed_request_host(header_host: str | None, port: int) -> bool:
    if not header_host:
        return False
    host = header_host.strip().lower()
    if host.startswith("["):
        return False
    name, _, port_s = host.partition(":")
    if name not in {"127.0.0.1", "localhost"}:
        return False
    if port_s and port_s != str(port):
        return False
    return True
