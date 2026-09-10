"""Shared export QuerySpec: scope, time bounds, unique job dirs, streaming writes."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from wechat_export.preview import record_presentation, analysis_record

SCOPE_ALL = "all"
SCOPE_CONVERSATIONS = "conversations"
FORMATS = ("jsonl", "csv", "md", "html")
INTERVAL = "[since,until)"
CSV_UNSAFE_PREFIX = ("=", "+", "-", "@", "\t", "\r")


class QueryError(ValueError):
    def __init__(self, message: str, code: str = "invalid_query") -> None:
        super().__init__(message)
        self.code = code


class ExportCancelled(RuntimeError):
    code = "cancelled"


def parse_time_to_ms(value: str | int | float | None) -> int | None:
    """Normalize a time value to UTC epoch milliseconds.

    Accepts epoch ms, epoch seconds, ISO-8601 with Z or offsets, and naive
    ISO datetimes (treated as UTC). Used so ``+00:00`` and ``.000Z`` compare equal.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QueryError("invalid time", "invalid_time")
    if isinstance(value, (int, float)):
        number = int(value)
        if number < 0:
            raise QueryError("invalid time", "invalid_time")
        if number < 10**11:
            number *= 1000
        return number
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+", text):
        return parse_time_to_ms(int(text))
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QueryError("invalid time", "invalid_time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def parse_query_bound(value, timezone_name: str) -> int | None:
    """Naive query inputs belong to the explicitly declared display timezone.

    A UTC round-trip rejects nonexistent wall times. Distinct valid fold=0/1
    instants require an explicit numeric offset, never a platform default guess.
    Canonical timestamp parsing remains a separate UTC-oriented contract.
    """
    if value is None or value == "" or isinstance(value, (int, float)):
        return parse_time_to_ms(value)
    text = str(value).strip()
    if not text or re.fullmatch(r"-?\d+", text):
        return parse_time_to_ms(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QueryError("Invalid calendar date/time", "invalid_time") from exc
    if parsed.tzinfo is not None:
        return parse_time_to_ms(text)
    zone = ZoneInfo(timezone_name)
    candidates = set()
    for fold in (0, 1):
        aware = parsed.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == parsed:
            candidates.add(int(utc.timestamp() * 1000))
    if not candidates:
        raise QueryError("此本地时间因夏令时跳转不存在，请选择有效时间。", "nonexistent_local_time")
    if len(candidates) != 1:
        raise QueryError("此本地时间重复出现，请附 UTC 偏移量（例如 -07:00 或 -08:00）。", "ambiguous_local_time")
    return candidates.pop()


def timestamp_utc_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    return parse_time_to_ms(value)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise QueryError("invalid timezone", "invalid_timezone") from exc
    return name


@dataclass(frozen=True)
class QuerySpec:
    """Canonical filter for preview and export. Empty conversation ids never mean all."""

    scope_kind: str
    conversation_ids: tuple[str, ...]
    since_ms: int | None
    until_ms: int | None
    readable_only: bool
    format: str
    display_timezone: str = "UTC"
    interval: str = INTERVAL
    message_types: tuple[str, ...] = ()
    mode: str = "raw"

    @classmethod
    def from_mapping(cls, body: dict[str, Any], *, known_ids: set[str] | None = None) -> "QuerySpec":
        if not isinstance(body, dict):
            raise QueryError("json object required", "invalid_schema")
        fmt = body.get("format") or "jsonl"
        if fmt not in FORMATS:
            raise QueryError("format must be jsonl, csv, md, or html", "invalid_format")
        mode = body.get("mode", "raw")
        if mode not in ("raw", "analysis"):
            raise QueryError("mode must be raw or analysis", "invalid_mode")
        tz = validate_timezone(str(body.get("display_timezone") or "UTC"))
        scope = body.get("scope")
        ids_raw = body.get("conversation_ids")
        if scope is None:
            if ids_raw:
                scope_kind = SCOPE_CONVERSATIONS
                ids = _normalize_ids(ids_raw)
            else:
                raise QueryError("scope is required; empty selection is not all", "scope_required")
        elif isinstance(scope, str):
            scope_kind = scope
            ids = _normalize_ids(ids_raw or [])
        elif isinstance(scope, dict):
            scope_kind = str(scope.get("kind") or "")
            ids = _normalize_ids(scope.get("conversation_ids") if "conversation_ids" in scope else ids_raw or [])
        else:
            raise QueryError("invalid scope", "invalid_scope")
        if scope_kind not in {SCOPE_ALL, SCOPE_CONVERSATIONS}:
            raise QueryError("scope.kind must be all or conversations", "invalid_scope")
        if scope_kind == SCOPE_CONVERSATIONS:
            if not ids:
                raise QueryError("conversation ids required", "empty_selection")
        else:
            ids = ()
        if known_ids is not None and scope_kind == SCOPE_CONVERSATIONS:
            unknown = [cid for cid in ids if cid not in known_ids]
            if unknown:
                raise QueryError("unknown conversation id", "unknown_conversation")
        since_ms = parse_query_bound(body.get("since"), tz)
        until_ms = parse_query_bound(body.get("until"), tz)
        if since_ms is not None and until_ms is not None and since_ms >= until_ms:
            raise QueryError("since must be earlier than until", "invalid_interval")
        types = body.get("message_types") or []
        if not isinstance(types, list) or len(types) > 30 or any(not isinstance(t, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", t) for t in types):
            raise QueryError("invalid message types", "invalid_types")
        readable = body.get("readable_only", False)
        if not isinstance(readable, bool):
            raise QueryError("readable_only must be a boolean", "invalid_schema")
        return cls(
            scope_kind=scope_kind,
            conversation_ids=ids,
            since_ms=since_ms,
            until_ms=until_ms,
            readable_only=readable,
            message_types=tuple(dict.fromkeys(types)),
            format=str(fmt),
            mode=mode,
            display_timezone=tz,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "scope": {"kind": self.scope_kind, "conversation_ids": list(self.conversation_ids)},
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "interval": self.interval,
            "readable_only": self.readable_only,
            "message_types": list(self.message_types),
            "format": self.format,
            "mode": self.mode,
            "display_timezone": self.display_timezone,
        }


def _normalize_ids(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise QueryError("conversation_ids must be a list", "invalid_schema")
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None or item is False:
            continue
        text = str(item).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        ids.append(text)
    return tuple(ids)


def csv_safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(CSV_UNSAFE_PREFIX):
        return "'" + text
    return text


def create_job_directory(parent: Path, job_id: str | None = None) -> Path:
    """Exclusive UUID directory. Concurrent same-second jobs cannot share a path."""
    if parent.is_symlink():
        raise QueryError("output parent must not be a symlink", "unsafe_output_parent")
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(8):
        name = job_id or str(uuid.uuid4())
        dest = parent / name
        try:
            dest.mkdir(mode=0o700, exist_ok=False)
            return dest
        except FileExistsError:
            if job_id:
                raise QueryError("job directory exists", "job_exists")
            continue
    raise QueryError("could not allocate job directory", "job_exists")


def atomic_publish(tmp_path: Path, final_path: Path) -> Path:
    if final_path.exists():
        raise QueryError("refusing to overwrite existing export", "already_exists")
    os.replace(tmp_path, final_path)
    return final_path


def _sql_filter(spec: QuerySpec) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if spec.scope_kind == SCOPE_CONVERSATIONS:
        clauses.append("conversation_id IN (%s)" % ",".join("?" * len(spec.conversation_ids)))
        params.extend(spec.conversation_ids)
    if spec.since_ms is not None:
        clauses.append("ts_ms(timestamp_utc) >= ?")
        params.append(spec.since_ms)
    if spec.until_ms is not None:
        clauses.append("ts_ms(timestamp_utc) < ?")
        params.append(spec.until_ms)
    if spec.message_types:
        clauses.append("message_type IN (%s)" % ",".join("?" * len(spec.message_types)))
        params.extend(spec.message_types)
    if spec.readable_only:
        clauses.append("readable = 1")
    return " AND ".join(clauses), params


def attach_time_function(conn: sqlite3.Connection) -> None:
    def _ts_ms(value: str | None) -> int:
        try:
            parsed = timestamp_utc_to_ms(value)
        except QueryError:
            return -1
        return -1 if parsed is None else parsed

    conn.create_function("ts_ms", 1, _ts_ms, deterministic=True)


def known_conversation_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT conversation_id FROM conversations").fetchall()
    if rows:
        return {str(r[0]) for r in rows if r[0]}
    rows = conn.execute("SELECT DISTINCT conversation_id FROM messages").fetchall()
    return {str(r[0]) for r in rows if r[0]}


def count_messages(conn: sqlite3.Connection, spec: QuerySpec) -> int:
    attach_time_function(conn)
    where, params = _sql_filter(spec)
    row = conn.execute(f"SELECT count(*) FROM messages WHERE {where}", params).fetchone()
    return int(row[0])


def selection_accounting(candidate_count: int, selected_count: int, readable_only: bool) -> dict:
    return {"schema": "wechat-selection-accounting/1",
            "scope": "same_conversations_time_and_types_before_readability_filter",
            "candidate_count": candidate_count, "selected_count": selected_count,
            "excluded_unreadable_count": candidate_count - selected_count,
            "readable_only": readable_only}


def count_selection(conn: sqlite3.Connection, spec: QuerySpec) -> dict:
    """One aggregate over the same scope/time/types, before readability filtering."""
    attach_time_function(conn)
    where, params = _sql_filter(replace(spec, readable_only=False))
    total, readable = conn.execute(
        f"SELECT count(*), coalesce(sum(CASE WHEN readable=1 THEN 1 ELSE 0 END),0) FROM messages WHERE {where}",
        params).fetchone()
    return selection_accounting(int(total), int(readable) if spec.readable_only else int(total), spec.readable_only)


def iter_index_messages(conn: sqlite3.Connection, spec: QuerySpec) -> Iterator[dict[str, Any]]:
    attach_time_function(conn)
    where, params = _sql_filter(spec)
    cur = conn.execute(
        f"SELECT * FROM messages WHERE {where} ORDER BY timestamp_utc, record_uid",
        params,
    )
    cols = [c[0] for c in cur.description]
    while True:
        row = cur.fetchone()
        if row is None:
            break
        yield dict(zip(cols, row, strict=True))


def record_matches_spec(rec: dict[str, Any], spec: QuerySpec) -> bool:
    if spec.message_types and (rec.get("message_type_normalized") or rec.get("message_type")) not in spec.message_types:
        return False
    if spec.scope_kind == SCOPE_CONVERSATIONS and rec.get("conversation_id") not in spec.conversation_ids:
        return False
    ts = timestamp_utc_to_ms(rec.get("timestamp_utc"))
    if spec.since_ms is not None and (ts is None or ts < spec.since_ms):
        return False
    if spec.until_ms is not None and (ts is None or ts >= spec.until_ms):
        return False
    if spec.readable_only and not record_presentation(rec)["readable"]:
        return False
    return True


def iter_canonical_messages(export_dir: Path, spec: QuerySpec, *, check=None) -> Iterator[dict[str, Any]]:
    path = export_dir / "all" / "messages.jsonl"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for position, line in enumerate(fh):
            if check is not None and position % 512 == 0:
                check()
            if not line.strip():
                continue
            rec = json.loads(line)
            if record_matches_spec(rec, spec):
                yield rec


def write_records(
    path: Path,
    records: Iterator[dict[str, Any]],
    fmt: str,
    *,
    should_cancel: Any | None = None,
    on_progress: Any | None = None,
    expected_count: int | None = None,
) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0

    def _tick() -> None:
        if should_cancel and should_cancel():
            raise ExportCancelled("export cancelled")
        if on_progress and count and count % 500 == 0:
            on_progress(count)

    try:
        if fmt == "jsonl":
            with tmp.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                    count += 1
                    _tick()
        elif fmt == "csv":
            fields = [
                "record_uid", "source_kind", "source_snapshot_id", "conversation_type",
                "sender_id", "is_self", "message_type_normalized", "parse_status",
                "timestamp_utc",
                "conversation_id",
                "sender_display_name",
                "media_kind",
                "preview",
                "text",
                "attachment_summary",
                "original_url",
            ]
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for rec in records:
                    from wechat_export.link_urls import safe_web_url
                    info = rec if rec.get('export_mode') == 'analysis' else record_presentation(rec)
                    rec = dict(rec, original_url=safe_web_url((info.get('card') or {}).get('url')))
                    writer.writerow({key: csv_safe_cell(json.dumps(rec.get(key),ensure_ascii=False,sort_keys=True) if key=="attachment_summary" else rec.get(key)) for key in fields})
                    count += 1
                    _tick()
        elif fmt in {"md", "html"}:
            from wechat_export.offline_html import document_header, document_row, FOOTER
            with tmp.open("w", encoding="utf-8") as fh:
                if fmt == "html":
                    fh.write(document_header("聊天导出"))
                else:
                    fh.write("# export\n\n")
                for rec in records:
                    from wechat_export.message_cards import card_text
                    rec = analysis_record(rec) if rec.get("export_mode") != "analysis" else rec
                    who = str(rec.get("sender_display_name") or "unknown")
                    text = str(rec.get("text") or rec.get("preview") or "")
                    kind = str(rec.get("message_type_normalized") or rec.get("media_kind") or "unknown")
                    if kind not in {"text", "system", "sys_event"} and text.lstrip().startswith("<"):
                        text = "[" + kind + "]"
                    if not text:
                        text = "[" + kind + "]"
                    details = card_text(rec.get("card"))
                    if details: text += "\n" + details
                    from wechat_export.attachment_accounting import attachment_note
                    note = attachment_note(rec.get("attachment_summary"))
                    if note: text += "\n" + note
                    stamp = str(rec.get("timestamp_utc") or "")
                    if fmt == "html":
                        fh.write(document_row(stamp, who, text, bool(rec.get('is_self')), card=rec.get('readable') is False, link_url=(rec.get('card') or {}).get('url')))
                    else:
                        fh.write(f"- {stamp} {who}: {text}\n")
                    count += 1
                    _tick()
                if fmt == "html":
                    fh.write(FOOTER)
        else:
            raise QueryError("unsupported format", "invalid_format")
        _tick()
        if expected_count is not None and count != expected_count:
            raise QueryError("Output count differs from the selected preview; no file published", "preview_count_mismatch")
        atomic_publish(tmp, path)
        return count
    except (ExportCancelled, QueryError):
        tmp.unlink(missing_ok=True)
        raise


def write_slice(
    conn: sqlite3.Connection,
    export_dir: Path,
    spec: QuerySpec,
    *,
    jobs_root: Path | None = None,
    source: str = "index",
    should_cancel: Any | None = None,
    on_progress: Any | None = None,
    expected_count: int | None = None,
    source_binding: dict[str, str] | None = None,
    media_probe=None,
) -> dict[str, Any]:
    if source not in {"canonical", "index"}:
        raise QueryError("unknown record source", "invalid_source")
    def check_cancel():
        if should_cancel and should_cancel():
            raise ExportCancelled('export cancelled')
    base_spec = replace(spec, readable_only=False)
    if source == "canonical":
        if not (export_dir / "all" / "messages.jsonl").is_file():
            raise QueryError("canonical source is missing; refusing index substitution", "canonical_source_missing")
        records = iter_canonical_messages(export_dir, base_spec, check=check_cancel)
    else:
        records = iter_index_messages(conn, base_spec)
    from wechat_export.scratch import ScratchSpace
    parent = jobs_root or (export_dir / "slices")
    dest = create_job_directory(parent)
    reserved = dest.stat()
    try:
        with ScratchSpace(parent, "slice-output") as temporary:
            stage = temporary.payload / 'export'
            stage.mkdir(mode=0o700)
            filename = {"jsonl": "messages.jsonl", "csv": "messages.csv", "md": "messages.md", "html": "messages.html"}[spec.format]
            path = stage / filename
            from wechat_export.attachment_accounting import AttachmentAccounting
            attachment_path = stage / 'attachment-ledger.jsonl'
            with attachment_path.open('w', encoding='utf-8') as ledger_stream:
                attachments = AttachmentAccounting(ledger_stream, probe=media_probe)
                selection = selection_accounting(0, 0, spec.readable_only)
                def accounted(source_records):
                    for record in source_records:
                        check_cancel()
                        selection['candidate_count'] += 1
                        if spec.readable_only and not (bool(record.get('readable')) if source == 'index' else record_presentation(record)['readable']):
                            selection['excluded_unreadable_count'] += 1
                            continue
                        selection['selected_count'] += 1
                        attachments.observe(record)
                        yield analysis_record(record) if spec.mode == 'analysis' else record
                count = write_records(
                    path, accounted(records), spec.format,
                    should_cancel=should_cancel, on_progress=on_progress, expected_count=expected_count,
                )
            attachment_path.chmod(0o600)
            source_manifest = {}
            if (export_dir / "manifest.json").is_file():
                source_manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
            # Unknown legacy lineage stays null; a slice cannot certify its source.
            lineage = {key: source_manifest.get(key) for key in (
                "schema_version", "parser_version", "source_snapshot_id", "source_snapshot_sha256",
                "records_complete", "records_complete_scope", "recognized_message_tables_complete",
                "attachments_complete", "coverage_verified", "database_accounting", "snapshot_inventory",
            )}
            manifest = {
                "source_lineage": lineage,
                "source_binding": source_binding,
                "record_source": source,
                "selection_accounting": selection,
                "attachment_accounting": attachments.summary(),
                "attachment_ledger": "attachment-ledger.jsonl",
                "job_id": dest.name,
                "count": count,
                "format": spec.format,
                "source_kind": "live-db",
                "backup2_coverage": "unverified",
                "query": spec.to_public_dict(),
                "interval": INTERVAL,
                "path": str(dest / filename),
            }
            from wechat_export.coverage_report import write_coverage
            write_coverage(stage, manifest)
            (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            path.chmod(0o600)
            (stage / 'manifest.json').chmod(0o600)
            if should_cancel and should_cancel():
                raise ExportCancelled('export cancelled')
            current = dest.lstat()
            if (current.st_dev, current.st_ino) != (reserved.st_dev, reserved.st_ino) or any(dest.iterdir()):
                raise QueryError('export destination changed', 'destination_changed')
            # The only replaced directory is our exclusive empty UUID reservation.
            # Data + manifest appear together, never a half-written public slice.
            os.rename(stage, dest)
        return manifest
    except BaseException:
        try:
            current = dest.lstat()
            if (current.st_dev, current.st_ino) == (reserved.st_dev, reserved.st_ino):
                dest.rmdir()  # only empty reservations; completed exports are retained
        except OSError:
            pass
        raise
