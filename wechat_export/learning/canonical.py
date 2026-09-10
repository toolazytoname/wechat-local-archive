"""URL keys for de-duplication. Do not strip article-identifying query params."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wechat_export.link_urls import safe_web_url

TRACKING = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "from",
    "isappinstalled",
    "nsukey",
    "scene",
    "clicktime",
}


def canonical_url_key(url: str | None) -> str | None:
    safe = safe_web_url(url) if url else None
    if not safe:
        return None
    parts = urlsplit(safe)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING]
    query = urlencode(kept)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path.rstrip("/") or "/", query, ""))
