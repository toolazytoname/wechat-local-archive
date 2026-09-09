from __future__ import annotations

import base64
from typing import Any

import zstandard

TYPE_MAP = {
    1: "text",
    3: "image",
    34: "voice",
    42: "card",
    43: "video",
    47: "sticker",
    48: "location",
    49: "app",
    10000: "system",
    10002: "sys_event",
}


def normalize_local_type(local_type: int | None) -> tuple[int | None, str]:
    if local_type is None:
        return None, "unknown"
    real = int(local_type) & 0xFFFF
    return real, TYPE_MAP.get(real, f"unknown_{real}")


def decode_message_content(value: Any, compression_flag: Any) -> tuple[str | None, str | None, list[str]]:
    notes: list[str] = []
    if value is None:
        return None, None, notes
    raw: bytes
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = str(value).encode("utf-8")
        notes.append("content_coerced_to_str")
    if compression_flag == 4:
        try:
            raw = zstandard.ZstdDecompressor().decompress(raw)
            notes.append("zstd_decompressed")
        except zstandard.ZstdError as exc:
            notes.append(f"zstd_failed:{type(exc).__name__}")
            return None, base64.b64encode(raw).decode("ascii"), notes
    try:
        return raw.decode("utf-8"), None, notes
    except UnicodeDecodeError:
        notes.append("not_utf8")
        return None, base64.b64encode(raw).decode("ascii"), notes
