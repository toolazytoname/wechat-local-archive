"""Turn stored message payloads into short, readable previews."""

from __future__ import annotations

import re

_TITLE = re.compile(r"<title>([^<]{1,80})</title>", re.I)
_XML = re.compile(r"^\s*(<\?xml|<msg[\s>]|<appmsg[\s>])", re.I)

_TYPE_LABEL = {
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "sticker": "表情",
    "app": "链接",
    "card": "名片",
    "location": "位置",
    "system": "系统",
}


def preview_message(text: str | None, type_name: str) -> tuple[str, bool]:
    """Return (preview, is_readable_text)."""
    if not text:
        return f"[{_TYPE_LABEL.get(type_name, type_name)}]", False
    stripped = text.strip()
    if _XML.match(stripped) or (stripped.startswith("<") and "</" in stripped[:400]):
        title = _TITLE.search(stripped)
        label = _TYPE_LABEL.get(type_name, type_name)
        if title:
            return f"[{label}] {title.group(1).strip()}", False
        return f"[{label}]", False
    compact = re.sub(r"\s+", " ", stripped)
    return compact[:400], True
