"""Build a local SQLite index over an export directory. Does not upload anything."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from wechat_export.preview import classify_payload

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY,
  conversation_type TEXT,
  display_name TEXT,
  message_count INTEGER,
  first_timestamp_utc TEXT,
  last_timestamp_utc TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  record_uid TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  conversation_type TEXT,
  sender_id TEXT,
  sender_display_name TEXT,
  is_self INTEGER,
  timestamp_utc TEXT,
  message_type TEXT,
  preview TEXT,
  readable INTEGER,
  source_kind TEXT,
  text TEXT,
  media_kind TEXT,
  media_title TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conversation_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_msg_readable ON messages(conversation_id, readable, timestamp_utc);
"""


def default_index_path(export_dir: Path) -> Path:
    return export_dir / "archive.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def build_index(export_dir: Path, index_path: Path | None = None) -> dict[str, Any]:
    export_dir = export_dir.resolve()
    index_path = index_path or default_index_path(export_dir)
    messages = export_dir / "all" / "messages.jsonl"
    convos = export_dir / "all" / "conversations.jsonl"
    manifest = export_dir / "manifest.json"
    if not messages.exists():
        raise FileNotFoundError(f"missing {messages}")

    if index_path.exists():
        index_path.unlink()
    wal = Path(str(index_path) + "-wal")
    shm = Path(str(index_path) + "-shm")
    wal.unlink(missing_ok=True)
    shm.unlink(missing_ok=True)

    conn = _connect(index_path)
    try:
        conn.executescript(SCHEMA)
        conv_rows = []
        if convos.exists():
            with convos.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    conv_rows.append(
                        (
                            rec.get("conversation_id"),
                            rec.get("conversation_type"),
                            rec.get("conversation_display_name"),
                            int(rec.get("count") or 0),
                            rec.get("first_timestamp_utc"),
                            rec.get("last_timestamp_utc"),
                        )
                    )
        conn.executemany(
            "INSERT OR REPLACE INTO conversations VALUES (?,?,?,?,?,?)",
            conv_rows,
        )

        batch: list[tuple] = []
        n = 0
        readable_n = 0
        with messages.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                info = classify_payload(rec.get("text"), rec.get("message_type_normalized") or rec.get("payload_kind") or "unknown")
                if rec.get("payload_kind") and rec["payload_kind"] != "text":
                    info["media_kind"] = rec["payload_kind"]
                    if rec.get("media_title"):
                        info["title"] = rec["media_title"]
                readable = bool(info["readable"])
                if readable:
                    readable_n += 1
                batch.append(
                    (
                        rec.get("record_uid"),
                        rec.get("conversation_id"),
                        rec.get("conversation_type"),
                        rec.get("sender_id"),
                        rec.get("sender_display_name"),
                        1 if rec.get("is_self") else 0,
                        rec.get("timestamp_utc"),
                        rec.get("message_type_normalized"),
                        info["preview"],
                        1 if readable else 0,
                        rec.get("source_kind"),
                        info["body"] if readable else None,
                        info["media_kind"],
                        info.get("title"),
                    )
                )
                n += 1
                if len(batch) >= 2000:
                    conn.executemany(
                        "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    batch.clear()
        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
        tz = "America/Los_Angeles"
        if manifest.exists():
            try:
                tz = json.loads(manifest.read_text(encoding="utf-8")).get("display_timezone") or tz
            except json.JSONDecodeError:
                pass
        meta = {
            "export_dir": export_dir.name,
            "message_count": str(n),
            "readable_count": str(readable_n),
            "conversation_count": str(len(conv_rows)),
            "display_timezone": tz,
        }
        if manifest.exists():
            meta["manifest"] = manifest.read_text(encoding="utf-8")
        conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", list(meta.items()))
        conn.commit()
        return {
            "index_path": str(index_path),
            "message_count": n,
            "readable_count": readable_n,
            "conversation_count": len(conv_rows),
        }
    finally:
        conn.close()
