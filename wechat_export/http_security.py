"""Local-only write-API guards: session cookie, Origin, CSRF, POST body limits."""

from __future__ import annotations

import json
import secrets
from typing import Any

from wechat_export.loopback import LOOPBACK_HOST

SESSION_COOKIE = "wla_session"
CSRF_HEADER = "X-CSRF-Token"
MAX_BODY_BYTES = 1_000_000
JSON_TYPE = "application/json"


class HttpGuardError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_header(token: str) -> str:
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400"


def parse_cookie(header: str | None, name: str = SESSION_COOKIE) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def allowed_origin(origin: str | None, port: int) -> bool:
    if not origin:
        return False
    origin = origin.strip()
    return origin in {f"http://{LOOPBACK_HOST}:{port}", f"http://localhost:{port}"}


def require_write_auth(*, cookie_header_value: str | None, csrf_header: str | None, origin: str | None, session_token: str, port: int) -> None:
    cookie = parse_cookie(cookie_header_value)
    if not cookie or cookie != session_token:
        raise HttpGuardError("session required", 403, "session_required")
    if not csrf_header or csrf_header != session_token:
        raise HttpGuardError("csrf required", 403, "csrf_required")
    if not allowed_origin(origin, port):
        raise HttpGuardError("origin not allowed", 403, "origin_denied")


def parse_content_length(raw: str | None, *, max_bytes: int = MAX_BODY_BYTES) -> int:
    if raw is None or raw == "":
        raise HttpGuardError("content-length required", 400, "content_length_required")
    if not re_int(raw):
        raise HttpGuardError("invalid content-length", 400, "invalid_content_length")
    length = int(raw)
    if length < 0:
        raise HttpGuardError("invalid content-length", 400, "invalid_content_length")
    if length > max_bytes:
        raise HttpGuardError("payload too large", 400, "payload_too_large")
    return length


def re_int(raw: str) -> bool:
    if raw.startswith("-"):
        return raw[1:].isdigit() and len(raw) > 1
    return raw.isdigit()


def require_json_content_type(content_type: str | None) -> None:
    if not content_type:
        raise HttpGuardError("content-type must be application/json", 400, "invalid_content_type")
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime != JSON_TYPE:
        raise HttpGuardError("content-type must be application/json", 400, "invalid_content_type")


def loads_object(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpGuardError("invalid json", 400, "invalid_json") from exc
    if not isinstance(payload, dict):
        raise HttpGuardError("json object required", 400, "invalid_schema")
    return payload


def public_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, HttpGuardError):
        return {"error": str(exc), "code": exc.code}
    return {"error": "bad request", "code": "bad_request"}
