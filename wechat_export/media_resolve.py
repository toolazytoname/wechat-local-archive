"""Resolve local image/voice/video files from XML md5 + hardlink map. No remote fetch."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from wechat_export.preview import extract_media_meta

JPEG = b"\xff\xd8\xff"
PNG = b"\x89PNG"
GIF = b"GIF8"
MP4 = b"ftyp"
SILK = b"#!SILK_V3"
SILK_PREFIXED = b"\x02#!SILK_V3"


def conversation_hash(conversation_id: str) -> str:
    return hashlib.md5(conversation_id.encode("utf-8")).hexdigest()


def sniff_media(data: bytes) -> str | None:
    if data.startswith(JPEG):
        return "image/jpeg"
    if data.startswith(PNG):
        return "image/png"
    if data.startswith(GIF):
        return "image/gif"
    if data[4:8] == MP4:
        return "video/mp4"
    if data.startswith(SILK) or data.startswith(SILK_PREFIXED):
        return "audio/silk"
    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        return "audio/wav"
    return None


def lookup_hardlink_stem(hardlink_db: Path, md5: str) -> str | None:
    if not hardlink_db.is_file():
        return None
    wal = Path(str(hardlink_db) + "-wal")
    if wal.is_file() and wal.stat().st_size:
        raise ValueError("hardlink_mapping_has_pending_wal")
    if hardlink_db.is_symlink():
        raise ValueError("hardlink_mapping_symlink")
    conn = sqlite3.connect(hardlink_db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in ("image_hardlink_info_v4", "image_hardlink_info"):
            if table not in tables:
                continue
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            md5_col = "md5" if "md5" in cols else None
            name_col = "file_name" if "file_name" in cols else ("filename" if "filename" in cols else None)
            if not md5_col or not name_col:
                continue
            row = conn.execute(
                f"SELECT {name_col} FROM {table} WHERE {md5_col} = ? LIMIT 1",
                (md5,),
            ).fetchone()
            if row and row[0]:
                return str(row[0])
    finally:
        conn.close()
    return None


def _safe_under(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
        root_r = root.resolve()
    except OSError:
        return None
    if resolved == root_r or str(resolved).startswith(str(root_r) + "/"):
        return resolved
    return None


def _iter_stems(stem: str) -> list[str]:
    names = [stem]
    for suffix in ("", "_t", "_h", "_t_M", "_M"):
        names.append(f"{stem}{suffix}")
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def resolve_media_file(
    *,
    media_root: Path | None,
    hardlink_db: Path | None,
    conversation_id: str,
    timestamp_utc: str | None,
    payload: str | None,
    type_name: str,
    md5: str | None = None,
    media_kind: str | None = None,
) -> dict[str, Any]:
    meta = extract_media_meta(payload, type_name)
    if md5:
        meta["md5"] = md5
    if media_kind:
        meta["media_kind"] = media_kind
    result: dict[str, Any] = {
        "media_kind": meta.get("media_kind"),
        "md5": meta.get("md5"),
        "duration_ms": meta.get("duration_ms"),
        "title": meta.get("title"),
        "found": False,
        "path": None,
        "mime": None,
        "status": "missing",
    }
    if not media_root or not media_root.exists():
        result["status"] = "no_media_root"
        return result
    md5 = meta.get("md5")
    stem = lookup_hardlink_stem(hardlink_db, md5) if hardlink_db and md5 else None
    if not stem:
        stem = md5
    year_month = None
    if timestamp_utc and len(timestamp_utc) >= 7:
        year_month = timestamp_utc[0:7].replace("-", "-")
        if len(timestamp_utc) >= 7:
            year_month = timestamp_utc[0:4] + "-" + timestamp_utc[5:7]
    conv = conversation_hash(conversation_id)
    kind = meta.get("media_kind")
    candidates: list[Path] = []
    if kind == "image" and stem:
        attach = media_root / "msg" / "attach" / conv
        months = [year_month] if year_month else []
        if attach.is_dir():
            months.extend(sorted((p.name for p in attach.iterdir() if p.is_dir()), reverse=True))
        for month in months:
            img_dir = attach / month / "Img"
            for name in _iter_stems(stem):
                for ext in (".dat", ".jpg", ".jpeg", ".png"):
                    candidates.append(img_dir / f"{name}{ext}")
    elif kind == "video" and stem:
        video_root = media_root / "msg" / "video"
        months = [year_month] if year_month else []
        if video_root.is_dir():
            months.extend(sorted((p.name for p in video_root.iterdir() if p.is_dir()), reverse=True))
        for month in months:
            candidates.append(video_root / month / f"{stem}.mp4")
            candidates.append(video_root / month / f"{stem}_thumb.jpg")
    elif kind == "voice" and stem:
        voice_dir = media_root / "msg" / "voice"
        candidates.append(voice_dir / f"{stem}.wav")
        candidates.append(voice_dir / f"{stem}.silk")
    for candidate in candidates:
        safe = _safe_under(media_root, candidate)
        if not safe or not safe.is_file():
            continue
        with safe.open("rb") as stream:
            head = stream.read(32)
        result["found"] = True
        result["path"] = str(safe)
        result["mime"] = sniff_media(head) or "application/octet-stream"
        result["status"] = "ok"
        return result
    result["status"] = "not_found"
    return result
