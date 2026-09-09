from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.models import MessageRecord

UNSAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def safe_label(name: str, fallback: str) -> str:
    cleaned = UNSAFE.sub("_", name).strip("._") or fallback
    return cleaned[:80]


def write_jsonl(path: Path, records: Iterable[MessageRecord]) -> int:
    ensure_dir(path.parent)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def write_csv(path: Path, records: list[MessageRecord]) -> None:
    ensure_dir(path.parent)
    fields = [
        "record_uid",
        "conversation_id",
        "conversation_display_name",
        "sender_id",
        "sender_display_name",
        "is_self",
        "timestamp_utc",
        "message_type_normalized",
        "text",
        "server_message_id",
        "source_kind",
        "source_relative_path",
        "parse_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row = rec.to_dict()
            w.writerow({k: row.get(k) for k in fields})


def write_monthly_markdown(
    out_dir: Path,
    records: list[MessageRecord],
    tz_name: str,
) -> list[str]:
    ensure_dir(out_dir)
    tz = ZoneInfo(tz_name)
    grouped: dict[str, list[MessageRecord]] = defaultdict(list)
    for rec in records:
        if rec.timestamp_utc:
            dt = datetime.fromisoformat(rec.timestamp_utc).astimezone(tz)
            key = dt.strftime("%Y-%m")
        else:
            key = "unknown-month"
        grouped[key].append(rec)
    written = []
    for month, items in sorted(grouped.items()):
        path = out_dir / f"{month}.md"
        lines = [f"# {items[0].conversation_display_name or items[0].conversation_id} {month}", ""]
        for rec in items:
            when = rec.timestamp_utc or "unknown-time"
            who = rec.sender_display_name or rec.sender_id or "unknown"
            body = rec.text if rec.text is not None else f"[{rec.message_type_normalized}]"
            body = body.replace("\r\n", "\n")
            lines.append(f"- {when} {who}: {body}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(str(path))
    return written


def write_conversations_index(path: Path, records: list[MessageRecord]) -> None:
    convos: dict[str, dict] = {}
    for rec in records:
        item = convos.setdefault(
            rec.conversation_id,
            {
                "conversation_id": rec.conversation_id,
                "conversation_type": rec.conversation_type,
                "conversation_display_name": rec.conversation_display_name,
                "count": 0,
                "first_timestamp_utc": rec.timestamp_utc,
                "last_timestamp_utc": rec.timestamp_utc,
            },
        )
        item["count"] += 1
        if rec.timestamp_utc and (item["first_timestamp_utc"] is None or rec.timestamp_utc < item["first_timestamp_utc"]):
            item["first_timestamp_utc"] = rec.timestamp_utc
        if rec.timestamp_utc and (item["last_timestamp_utc"] is None or rec.timestamp_utc > item["last_timestamp_utc"]):
            item["last_timestamp_utc"] = rec.timestamp_utc
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as fh:
        for cid in sorted(convos):
            fh.write(json.dumps(convos[cid], ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest(path: Path, payload: dict) -> None:
    write_json(path, payload)
