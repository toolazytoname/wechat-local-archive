"""Build a local SQLite index over an export directory. Does not upload anything."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from wechat_export.export_service import timestamp_utc_to_ms
from wechat_export.fsutil import sha256_file
from wechat_export.archive_files import source_paths as archive_source_paths
from wechat_export.preview import record_presentation, PRESENTATION_VERSION

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
  timestamp_ms INTEGER,
  message_type TEXT,
  preview TEXT,
  readable INTEGER,
  source_kind TEXT,
  text TEXT,
  media_kind TEXT,
  media_title TEXT,
  media_md5 TEXT,
  duration_ms INTEGER,
  card_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conversation_id, timestamp_utc, record_uid);
CREATE INDEX IF NOT EXISTS idx_msg_readable ON messages(conversation_id, readable, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_msg_ts_ms ON messages(conversation_id, timestamp_ms);
"""

INSERT_SQL = "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


def default_index_path(export_dir: Path) -> Path:
    return export_dir / "archive.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def build_index(export_dir: Path, index_path: Path | None = None) -> dict[str, Any]:
    from wechat_export.scratch import ScratchSpace
    index_path = index_path or default_index_path(export_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with ScratchSpace(index_path.parent, "archive-index") as temporary:
        return _build_index(export_dir, index_path, temporary.payload / 'index.sqlite')


def _build_index(export_dir: Path, index_path: Path, building: Path) -> dict[str, Any]:
    export_dir = export_dir.resolve()
    index_path = index_path or default_index_path(export_dir)
    messages = export_dir / "all" / "messages.jsonl"
    convos = export_dir / "all" / "conversations.jsonl"
    manifest = export_dir / "manifest.json"
    if not messages.exists():
        raise FileNotFoundError(f"missing {messages}")

    source_paths = archive_source_paths(export_dir)
    source_hashes = {key: sha256_file(path) if path.is_file() else "absent" for key, path in source_paths.items()}
    conn = _connect(building)
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
                info = record_presentation(rec)
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
                        timestamp_utc_to_ms(rec.get("timestamp_utc")),
                        rec.get("message_type_normalized"),
                        info["preview"],
                        1 if readable else 0,
                        rec.get("source_kind"),
                        info["body"] if readable else None,
                        info["media_kind"],
                        info.get("title"),
                        info.get("md5"),
                        info.get("duration_ms"),
                        json.dumps(info.get("card"), ensure_ascii=False) if info.get("card") else None,
                    )
                )
                n += 1
                if len(batch) >= 2000:
                    conn.executemany(INSERT_SQL, batch)
                    batch.clear()
        if batch:
            conn.executemany(INSERT_SQL, batch)
        tz = "America/Los_Angeles"
        if manifest.exists():
            try:
                tz = json.loads(manifest.read_text(encoding="utf-8")).get("display_timezone") or tz
            except json.JSONDecodeError:
                pass
        meta = {
            **source_hashes,
            "presentation_version": PRESENTATION_VERSION,
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
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        if source_hashes != {key: sha256_file(path) if path.is_file() else "absent" for key, path in source_paths.items()}:
            raise ValueError("Canonical source changed during index construction")
        wal = Path(str(index_path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("Existing index has pending writes; refusing to replace it")
        building.chmod(0o600)
        os.replace(building, index_path)
        Path(str(building) + "-wal").unlink(missing_ok=True)
        Path(str(building) + "-shm").unlink(missing_ok=True)
        return {
            "index_path": str(index_path),
            "message_count": n,
            "readable_count": readable_n,
            "conversation_count": len(conv_rows),
        }
    except Exception:
        conn.close()
        building.unlink(missing_ok=True)
        Path(str(building) + "-wal").unlink(missing_ok=True)
        Path(str(building) + "-shm").unlink(missing_ok=True)
        raise


def ensure_index_current(export_dir: Path) -> Path:
    """Refresh derived presentation without touching canonical JSONL or source DBs."""
    import fcntl
    index = default_index_path(export_dir)
    with (export_dir / ".presentation-index.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if index.is_file():
            conn = None
            try:
                conn = sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)
                values = dict(conn.execute("SELECT key,value FROM meta"))
                paths = archive_source_paths(export_dir)
                if values.get("presentation_version") == PRESENTATION_VERSION and all(values.get(key) == (sha256_file(path) if path.is_file() else "absent") for key,path in paths.items()):
                    return index
            except sqlite3.Error:
                pass
            finally:
                if conn is not None:
                    conn.close()
        build_index(export_dir, index)
        return index
