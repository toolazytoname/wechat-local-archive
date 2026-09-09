"""Classify stored payloads into readable text vs structured media."""

from __future__ import annotations

import re
from typing import Any

_TITLE = re.compile(r"<title>([^<]{1,120})</title>", re.I)
_XML_START = re.compile(r"^\s*(<\?xml|<msg[\s>]|<appmsg[\s>]|<voicemsg[\s>])", re.I)
_ENVELOPE = re.compile(r"^([^\n\r:<]{1,80}):\r?\n", re.M)

_TYPE_LABEL = {
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "sticker": "表情",
    "app": "链接",
    "card": "名片",
    "location": "位置",
    "system": "系统",
    "sys_event": "系统",
}


def split_sender_envelope(text: str) -> tuple[str | None, str]:
    match = _ENVELOPE.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end() :]


def _looks_like_xml(body: str) -> bool:
    stripped = body.lstrip()
    if _XML_START.match(stripped):
        return True
    if stripped.startswith("<") and "</" in stripped[:800]:
        return True
    return False


def infer_media_kind(body: str, type_name: str) -> str:
    low = body[:2000].lower()
    if type_name in _TYPE_LABEL and type_name not in {"system", "sys_event", "text"}:
        return type_name
    if "<voicemsg" in low:
        return "voice"
    if "<img" in low or "cdnbigimgurl" in low:
        return "image"
    if "<videomsg" in low:
        return "video"
    if "<appmsg" in low:
        return "app"
    if type_name.startswith("unknown"):
        return "unknown"
    return type_name if type_name != "text" else "unknown"


def classify_payload(text: str | None, type_name: str) -> dict[str, Any]:
    """Split envelope, then classify. Unknown XML is never readable text."""
    if not text:
        kind = type_name if type_name != "text" else "unknown"
        label = _TYPE_LABEL.get(kind, kind)
        return {
            "sender_prefix": None,
            "body": "",
            "preview": f"[{label}]",
            "readable": False,
            "media_kind": kind,
            "title": None,
        }
    prefix, body = split_sender_envelope(text)
    if _looks_like_xml(body):
        kind = infer_media_kind(body, type_name)
        title_m = _TITLE.search(body)
        title = title_m.group(1).strip() if title_m else None
        label = _TYPE_LABEL.get(kind, kind)
        preview = f"[{label}] {title}".strip() if title else f"[{label}]"
        return {
            "sender_prefix": prefix,
            "body": body,
            "preview": preview,
            "readable": False,
            "media_kind": kind,
            "title": title,
        }
    compact = re.sub(r"\s+", " ", body.strip())
    if type_name.startswith("unknown") and not compact:
        return {
            "sender_prefix": prefix,
            "body": body,
            "preview": f"[{type_name}]",
            "readable": False,
            "media_kind": "unknown",
            "title": None,
        }
    return {
        "sender_prefix": prefix,
        "body": body.strip(),
        "preview": compact[:400],
        "readable": True,
        "media_kind": "text" if type_name in {"text", "system", "sys_event"} else type_name,
        "title": None,
    }


def preview_message(text: str | None, type_name: str) -> tuple[str, bool]:
    info = classify_payload(text, type_name)
    return info["preview"], info["readable"]
