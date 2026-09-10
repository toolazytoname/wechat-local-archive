"""Classify stored payloads into readable text vs structured media."""

from __future__ import annotations

import re
import html
from typing import Any

_TITLE = re.compile(r"<title>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</title>", re.I | re.S)
PRESENTATION_VERSION = "6"
_XML_START = re.compile(r"^(?:<\?xml\b|<!\s*(?:DOCTYPE|ENTITY)\b|<!\[CDATA\[|<!--|<[A-Za-z_][\w:.-]*(?:[\s/>]))", re.I)
_ENVELOPE = re.compile(r"^([^\n\r:<]{1,80}):\r?\n", re.M)
_MD5 = re.compile(r'\bmd5="([0-9a-fA-F]{32})"', re.I)
_URL = re.compile(r"<url>\s*([^<\s]+)\s*</url>", re.I)
_VOICE_LEN = re.compile(r'\bvoicelength="(\d+)"', re.I)
_FILEEXT = re.compile(r"<fileext>\s*([^<]+)\s*</fileext>", re.I)

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
    stripped = body.lstrip().lstrip("\ufeff").lstrip()
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
    # A typed "Alice:\nhello" is not evidence of a transport envelope.
    # Only a structured payload after the prefix is stripped here; source-aware
    # parsing can supply a separate sender field without deleting ordinary text.
    if prefix is not None and not _looks_like_xml(body):
        prefix, body = None, text
    if _looks_like_xml(body):
        kind = infer_media_kind(body, type_name)
        title_m = _TITLE.search(body)
        title = html.unescape((title_m.group(1) if title_m.group(1) is not None else title_m.group(2)).strip())[:500] if title_m else None
        if title and _looks_like_xml(title):
            title = None  # A nested payload is not a human title.
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


def extract_media_meta(text: str | None, type_name: str) -> dict[str, Any]:
    info = classify_payload(text, type_name)
    body = info.get("body") or text or ""
    md5s = _MD5.findall(body)
    url_m = _URL.search(body)
    voice_m = _VOICE_LEN.search(body)
    ext_m = _FILEEXT.search(body)
    info["md5"] = md5s[0].lower() if md5s else None
    info["url"] = url_m.group(1).replace("&amp;", "&") if url_m else None
    info["duration_ms"] = int(voice_m.group(1)) if voice_m else None
    info["fileext"] = (ext_m.group(1).strip().lstrip(".") if ext_m else None)
    return info


def record_presentation(rec: dict[str, Any]) -> dict[str, Any]:
    """One interpretation for the index, canonical filtering and analysis views."""
    type_name = rec.get("message_type_normalized") or rec.get("message_type") or rec.get("payload_kind") or "unknown"
    info = extract_media_meta(rec.get("text"), type_name)
    if rec.get("payload_kind") and rec["payload_kind"] != "text":
        info["media_kind"] = rec["payload_kind"]
        if rec.get("media_title"):
            info["title"] = rec["media_title"]
    if not info["readable"]:
        from wechat_export.message_cards import card_details
        card = card_details(info.get("body") or "")
        if card is not None:
            info["card"] = card
            if card["kind"] in {"file", "quote", "forwarded", "link"}:
                info["media_kind"] = card["kind"]
                label = {"file": "文件", "quote": "引用", "forwarded": "合并转发", "link": "链接"}[card["kind"]]
                info["preview"] = f"[{label}] " + (card.get("title") or "")
                info["title"] = card.get("title")
    return info


_ANALYSIS_FIELDS = (
    "schema_version", "record_uid", "account_id", "conversation_id", "conversation_type",
    "conversation_display_name", "sender_id", "sender_display_name", "is_self",
    "timestamp_utc", "timestamp_raw", "timestamp_unit", "display_timezone",
    "message_type_normalized", "server_message_id", "local_message_id",
    "source_kind", "source_snapshot_id", "source_relative_path", "source_table",
    "source_row_id", "parser_version", "parse_status",
)


def analysis_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted analysis projection, not a destructive change to the archive.

    This is not anonymization: message text and names remain sensitive. Structured
    payload bodies, arbitrary extra fields, binary payloads and attachment URLs
    never pass through merely because they exist in a canonical record.
    """
    info = record_presentation(rec)
    result = {key: rec[key] for key in _ANALYSIS_FIELDS if key in rec}
    from wechat_export.attachment_accounting import attachment_summary
    result['attachment_summary'] = attachment_summary(rec, info)
    result.update(text=info["body"] if info["readable"] else None,
                  preview=info["preview"], readable=bool(info["readable"]),
                  media_kind=info["media_kind"], media_title=info.get("title"),
                  duration_ms=info.get("duration_ms"), card=info.get("card"), export_mode="analysis")
    return result
