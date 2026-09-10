"""Private derived SQLite for identity, profiles and learning items."""

from __future__ import annotations

import fcntl
import tempfile
import time
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA journal_mode = DELETE;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  self_sender_ids TEXT NOT NULL,
  verification_state TEXT NOT NULL,
  notes TEXT,
  revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY,
  sender_ids TEXT NOT NULL,
  display_aliases TEXT NOT NULL,
  relationship TEXT,
  source TEXT NOT NULL,
  confirmed_at TEXT,
  revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_roles (
  conversation_id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  profile_excluded INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject_person_id TEXT,
  scope_json TEXT NOT NULL,
  identity_revision INTEGER NOT NULL,
  engine_id TEXT NOT NULL,
  status TEXT NOT NULL,
  coverage_json TEXT,
  result_json TEXT,
  error_code TEXT,
  processed_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  source_revision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  statement TEXT NOT NULL,
  basis TEXT NOT NULL,
  context_scope TEXT,
  evidence_json TEXT NOT NULL,
  caveats_json TEXT NOT NULL,
  review_state TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  record_uid TEXT NOT NULL,
  conversation_id TEXT,
  sender_id TEXT,
  author_role TEXT,
  quote TEXT NOT NULL,
  body_hash TEXT,
  content_origin TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  action TEXT NOT NULL,
  user_text TEXT,
  excluded_evidence_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_items (
  item_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  display_title TEXT NOT NULL,
  reading_state TEXT NOT NULL,
  content_state TEXT NOT NULL,
  topics_json TEXT NOT NULL,
  canonical_key TEXT,
  revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  active_content_id TEXT
);
CREATE TABLE IF NOT EXISTS item_sources (
  source_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  record_uid TEXT NOT NULL,
  saved_at TEXT,
  saved_comment TEXT,
  original_url TEXT,
  fragment_id TEXT
);
CREATE TABLE IF NOT EXISTS content_versions (
  content_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  acquisition TEXT NOT NULL,
  body_hash TEXT,
  available_extent TEXT NOT NULL,
  source_url TEXT,
  acquired_at TEXT NOT NULL,
  paragraph_json TEXT,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  note_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  content_id TEXT,
  quote_span TEXT,
  user_text TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_cards (
  card_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  content_id TEXT,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  citation_json TEXT NOT NULL,
  user_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_summaries (
  summary_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  engine_id TEXT NOT NULL,
  claims_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consent_tickets (
  ticket_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  engine_id TEXT NOT NULL,
  endpoint TEXT,
  model TEXT,
  scope_hash TEXT NOT NULL,
  record_uids TEXT NOT NULL,
  source_revision TEXT NOT NULL,
  identity_revision INTEGER,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sources_item ON item_sources(item_id);
CREATE INDEX IF NOT EXISTS idx_sources_uid ON item_sources(record_uid);
CREATE INDEX IF NOT EXISTS idx_items_key ON learning_items(canonical_key);
"""


class InsightsError(ValueError):
    def __init__(self, message: str, code: str = "insights_error") -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def namespace_for(source_revision: str) -> str:
    if not source_revision or "/" in source_revision or ".." in source_revision:
        raise InsightsError("invalid archive namespace", "invalid_namespace")
    return source_revision[:32]


def namespace_for_archive(archive_root: Path) -> str:
    resolved = str(Path(archive_root).resolve())
    if not resolved:
        raise InsightsError("invalid archive namespace", "invalid_namespace")
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:32]


class InsightStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.content_root = self.root / "content"
        self.content_root.mkdir(exist_ok=True)
        try:
            os.chmod(self.content_root, 0o700)
        except OSError:
            pass
        self.path = self.root / "insights.sqlite"
        new = not self.path.exists()
        self.conn = sqlite3.connect(str(self.path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        if new:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(learning_items)")}
        if "active_content_id" not in cols:
            self.conn.execute("ALTER TABLE learning_items ADD COLUMN active_content_id TEXT")

    def close(self) -> None:
        self.conn.close()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()


_USER_TABLES = (
    "identity",
    "people",
    "conversation_roles",
    "profile_runs",
    "observations",
    "evidence",
    "corrections",
    "learning_items",
    "item_sources",
    "content_versions",
    "notes",
    "review_cards",
    "learning_summaries",
    "consent_tickets",
)


def store_has_user_data(store: InsightStore) -> bool:
    for table in ("identity", "people", "learning_items", "notes", "profile_runs", "conversation_roles"):
        if store.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return True
    return False


def _is_transient_namespace(name: str) -> bool:
    return (
        ".backup-" in name
        or ".conflict-" in name
        or name.endswith(".staging")
        or name.endswith(".migrate.lock")
    )


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_files_complete(store: InsightStore) -> bool:
    for row in store.conn.execute("SELECT content_id, body_hash FROM content_versions"):
        digest = row["body_hash"]
        path = store.content_root / f"{digest or row['content_id']}.txt"
        if not path.is_file():
            path = store.content_root / f"{row['content_id']}.txt"
        if not path.is_file() or path.is_symlink():
            return False
        if digest and _file_digest(path) != digest:
            return False
    return True


def _copy_content_files(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.iterdir():
        if path.is_symlink():
            raise OSError("migration source contains a symbolic link")
        if not path.is_file():
            continue
        digest = _file_digest(path)
        target = dest / path.name
        if target.is_file() and not target.is_symlink() and _file_digest(target) == digest:
            continue
        fd, name = tempfile.mkstemp(prefix=".copy-", dir=dest)
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as output, path.open("rb") as stream:
                shutil.copyfileobj(stream, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if _file_digest(temp) != digest:
                raise OSError("migration content changed during copy")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


def _copy_user_rows(src: InsightStore, dest: InsightStore) -> None:
    dest.conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in _USER_TABLES:
            rows = src.conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            src_cols = [info[1] for info in src.conn.execute(f"PRAGMA table_info({table})")]
            dest_cols = {info[1] for info in dest.conn.execute(f"PRAGMA table_info({table})")}
            cols = [name for name in src_cols if name in dest_cols]
            placeholders = ",".join("?" * len(cols))
            sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES ({placeholders})"
            dest.conn.executemany(sql, [tuple(row[name] for name in cols) for row in rows])
        for row in src.conn.execute("SELECT key, value FROM meta"):
            if row["key"] in {"source_revision", "archive_root", "schema_version", "migration_status"}:
                continue
            dest.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                (row["key"], row["value"]),
            )
        dest.conn.commit()
    except Exception:
        dest.conn.rollback()
        raise
    finally:
        dest.conn.execute("PRAGMA foreign_keys = ON")


def _find_legacy_root(data_root: Path, source_revision: str, archive_root: Path, new_ns: str) -> Path | str | None:
    primary = data_root / "insights" / namespace_for(source_revision)
    insights = data_root / "insights"
    if not insights.is_dir():
        return None
    archive_resolved = str(Path(archive_root).resolve())
    matched: list[Path] = []
    unmatched: list[Path] = []
    for child in insights.iterdir():
        if child.is_symlink() or not child.is_dir() or _is_transient_namespace(child.name) or child.name == new_ns:
            continue
        db = child / "insights.sqlite"
        if not db.is_file() or db.is_symlink():
            continue
        # Discovery must not upgrade or write unrelated derived databases.
        probe = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            meta = dict(probe.execute("SELECT key, value FROM meta"))
            if meta.get("migration_status") == "migrated_out":
                continue
            stored_root = meta.get("archive_root")
            if stored_root:
                if str(Path(stored_root).resolve()) == archive_resolved:
                    matched.append(child)
                continue  # Explicit different binding wins over a revision prefix.
            if child == primary and meta.get("source_revision") == source_revision:
                matched.append(child)
            elif any(probe.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                     for table in ("identity", "learning_items", "notes", "people", "conversation_roles", "profile_runs")):
                unmatched.append(child)
        except sqlite3.DatabaseError:
            unmatched.append(child)
        finally:
            probe.close()
    if len(matched) == 1:
        return matched[0]
    if matched or unmatched:
        return "needs_recovery"
    return None


def _mark_conflict(data_root: Path, new_ns: str, old_root: Path, dest: InsightStore) -> str:
    stamp = utc_now().replace(":", "").replace("+", "")
    conflict = data_root / "insights" / f"{new_ns}.conflict-{stamp}"
    if not conflict.exists():
        shutil.copytree(old_root, conflict)
    dest.set_meta("migration_status", "conflict")
    dest.set_meta("legacy_conflict", str(conflict))
    dest.set_meta("legacy_namespace", old_root.name)
    return "conflict"


def _apply_legacy_copy(src: InsightStore, dest: InsightStore) -> None:
    dest.set_meta("migration_status", "incomplete")
    _copy_user_rows(src, dest)
    _copy_content_files(src.content_root, dest.content_root)
    if not _content_files_complete(dest):
        dest.set_meta("migration_status", "failed")
        raise OSError("migration content missing")


def migrate_legacy_namespace(data_root: Path, source_revision: str, archive_root: Path, dest: InsightStore) -> str:
    """Move old revision[:32] stores into the archive-root namespace. Never silent-overwrite."""
    new_ns = namespace_for_archive(archive_root)
    existing = dest.get_meta("migration_status")
    if existing in {"migrated", "conflict", "needs_recovery"}:
        return existing
    found = _find_legacy_root(data_root, source_revision, archive_root, new_ns)
    if found == "needs_recovery":
        dest.set_meta("migration_status", "needs_recovery")
        return "needs_recovery"
    if found is None:
        return dest.get_meta("migration_status") or "no_legacy"
    old_root = found
    if old_root.resolve() == dest.root.resolve():
        return dest.get_meta("migration_status") or "same_namespace"
    src = InsightStore(old_root)
    staging: InsightStore | None = None
    try:
        if (src.get_meta("migration_status") or "") == "migrated_out" and src.get_meta("migrated_to") == new_ns:
            return dest.get_meta("migration_status") or "already_migrated"
        if not store_has_user_data(src):
            return dest.get_meta("migration_status") or "legacy_empty"
        # Never replay old rows into a populated destination, including a failed
        # migration from an older release: it may contain newer user edits.
        if store_has_user_data(dest):
            return _mark_conflict(data_root, new_ns, old_root, dest)
        stamp = utc_now().replace(":", "").replace("+", "")
        backup = data_root / "insights" / f"{old_root.name}.backup-{stamp}"
        if not backup.exists():
            shutil.copytree(old_root, backup)
        staging_root = data_root / "insights" / f"{new_ns}.staging"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging = InsightStore(staging_root)
        _apply_legacy_copy(src, staging)
        staging.set_meta("migration_status", "migrated")
        staging.set_meta("migrated_from", old_root.name)
        staging.set_meta("migrated_backup", str(backup))
        # Content first, SQLite publish last. No business caller can open the
        # destination while open_store holds its process-owned lock.
        _copy_content_files(staging.content_root, dest.content_root)
        if not _content_files_complete(staging):
            raise OSError("migration content failed validation")
        for row in staging.conn.execute("SELECT body_hash FROM content_versions"):
            if row[0] and _file_digest(dest.content_root / f"{row[0]}.txt") != row[0]:
                raise OSError("migration destination content failed validation")
        staging.close()
        staging = None
        dest.close()
        try:
            os.replace(staging_root / "insights.sqlite", dest.path)
        finally:
            # Keep error cleanup usable even when publication fails.
            dest.__init__(dest.root)
        shutil.rmtree(staging_root, ignore_errors=True)
        src.set_meta("migration_status", "migrated_out")
        src.set_meta("migrated_to", new_ns)
        return "migrated"
    except Exception:
        if dest.get_meta("migration_status") not in {"conflict", "needs_recovery"}:
            dest.set_meta("migration_status", "failed")
        leftover = data_root / "insights" / f"{new_ns}.staging"
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)
        raise
    finally:
        if staging is not None:
            staging.close()
        src.close()


def open_store(data_root: Path, source_revision: str, *, archive_root: Path | None = None) -> InsightStore:
    ns = namespace_for_archive(archive_root) if archive_root is not None else namespace_for(source_revision)
    parent = data_root / "insights"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never unlink this inode: flock is released by the OS on crash/exit.
    fd = os.open(parent / f"{ns}.open.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    store = None
    try:
        deadline = time.monotonic() + 0.25
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InsightsError("Archive initialization is in progress; retry shortly", "migration_in_progress") from None
                time.sleep(0.01)
        store = InsightStore(parent / ns)
        if archive_root is not None:
            migrate_legacy_namespace(data_root, source_revision, archive_root, store)
            store.set_meta("archive_root", str(Path(archive_root).resolve()))
        store.set_meta("source_revision", source_revision)
        return store
    except Exception:
        if store is not None:
            store.close()
        raise
    finally:
        os.close(fd)
