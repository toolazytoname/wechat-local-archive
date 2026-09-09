"""Minimal protobuf field walker. Values are not logged by default."""

from __future__ import annotations

from typing import Any


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = 0
    n = 0
    while i < len(data):
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        if b < 0x80:
            return n, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def walk(data: bytes) -> list[dict[str, Any]]:
    i = 0
    fields: list[dict[str, Any]] = []
    while i < len(data):
        key, i = _read_varint(data, i)
        field = key >> 3
        wire = key & 7
        rec: dict[str, Any] = {"field": field, "wire": wire}
        if wire == 0:
            value, i = _read_varint(data, i)
            rec["varint"] = value
        elif wire == 1:
            rec["len"] = 8
            i += 8
        elif wire == 2:
            ln, i = _read_varint(data, i)
            rec["len"] = ln
            blob = data[i : i + ln]
            i += ln
            rec["ascii"] = blob.decode("utf-8", errors="replace") if ln <= 256 else None
            rec["looks_utf8"] = True
            try:
                blob.decode("utf-8")
            except UnicodeDecodeError:
                rec["looks_utf8"] = False
                rec["ascii"] = None
        elif wire == 5:
            rec["len"] = 4
            i += 4
        else:
            rec["error"] = f"unsupported wire {wire}"
            break
        fields.append(rec)
    return fields
