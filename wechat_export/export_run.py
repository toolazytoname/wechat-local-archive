from __future__ import annotations

from pathlib import Path
from typing import Any

from wechat_export.config import AppConfig
from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.livedb_export import find_targets, iter_messages, load_contacts
from wechat_export.models import MessageRecord
from wechat_export.quality import build_quality_report, write_quality_report
from wechat_export.writers import (
    safe_label,
    write_conversations_index,
    write_csv,
    write_jsonl,
    write_manifest,
    write_monthly_markdown,
)

DEFAULT_TARGET_NAMES: tuple[str, ...] = ()


def sort_key(rec: MessageRecord) -> tuple:
    return (
        rec.timestamp_utc or "",
        rec.source_relative_path or "",
        rec.source_table or "",
        rec.local_message_id or "",
        rec.record_uid,
    )


def collect_records(
    decrypted_root: Path,
    cfg: AppConfig,
    source_kind: str,
    source_snapshot_id: str | None,
) -> tuple[list[MessageRecord], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    contact_db = decrypted_root / "contact" / "contact.db"
    if not contact_db.exists():
        # allow flat layout
        contact_db = decrypted_root / "contact.db"
    contacts = load_contacts(contact_db) if contact_db.exists() else {}
    names = list(cfg.target_names) if cfg.target_names else list(DEFAULT_TARGET_NAMES)
    targets = find_targets(contacts, names)
    self_usernames = {cfg.account_backup_root.name, cfg.live_account_root.name.split("_")[0]}
    records: list[MessageRecord] = []
    message_root = decrypted_root / "message"
    if not message_root.exists():
        message_root = decrypted_root
    dbs = sorted(message_root.glob("message_*.db")) + sorted(message_root.glob("biz_message_*.db"))
    if not dbs:
        dbs = sorted(p for p in message_root.glob("*.db") if p.name.startswith("message") or p.name.startswith("biz_"))
    db_status = []
    for db in dbs:
        if "fts" in db.name or "resource" in db.name:
            db_status.append({"path": db.name, "status": "skipped_index_or_resource"})
            continue
        before = len(records)
        records.extend(
            iter_messages(
                db,
                account_id=cfg.account_backup_root.name,
                source_kind=source_kind,
                source_snapshot_id=source_snapshot_id,
                display_timezone=cfg.display_timezone,
                contacts=contacts,
                self_usernames=self_usernames,
            )
        )
        db_status.append({"path": db.name, "status": "ok", "records": len(records) - before})
    records.sort(key=sort_key)
    return records, targets, {"contacts": len(contacts), "databases": db_status}


def export_records(
    records: list[MessageRecord],
    targets: dict[str, list[dict[str, Any]]],
    cfg: AppConfig,
    run_id: str,
    *,
    source_kind: str,
    backup2_coverage: str,
    extra_notes: list[str],
) -> Path:
    out = ensure_dir(cfg.exports_root / run_id)
    all_dir = ensure_dir(out / "all")
    write_jsonl(all_dir / "messages.jsonl", records)
    write_csv(all_dir / "messages.csv", records)
    write_conversations_index(all_dir / "conversations.jsonl", records)
    by_conv: dict[str, list[MessageRecord]] = {}
    for rec in records:
        by_conv.setdefault(rec.conversation_id, []).append(rec)
    for cid, items in by_conv.items():
        label = safe_label(items[0].conversation_display_name or cid, cid[:16])
        write_monthly_markdown(out / "conversations" / f"{label}__{safe_label(cid, 'id')}", items, cfg.display_timezone)

    target_export_counts = {}
    for name, matches in targets.items():
        chosen = matches[0]["username"] if len(matches) == 1 else None
        subset = [r for r in records if chosen and r.conversation_id == chosen]
        tdir = ensure_dir(out / "targets" / safe_label(name, "target"))
        write_jsonl(tdir / "messages.jsonl", subset)
        write_csv(tdir / "messages.csv", subset)
        write_monthly_markdown(tdir, subset, cfg.display_timezone)
        target_export_counts[name] = {
            "match_count": len(matches),
            "ambiguous": len(matches) != 1,
            "chosen_conversation_id": chosen,
            "exported_records": len(subset),
        }

    export_status = "complete" if records else "blocked"
    if any(v["ambiguous"] or v["match_count"] == 0 for v in target_export_counts.values()):
        if export_status == "complete":
            export_status = "partial"

    notes = list(extra_notes)
    notes.append(f"target_resolution: {target_export_counts}")
    report = build_quality_report(
        records,
        source_kind=source_kind,
        backup2_coverage=backup2_coverage,
        export_status=export_status,
        extra_lines=notes,
    )
    write_quality_report(out / "quality-report.md", report)
    write_manifest(
        out / "manifest.json",
        {
            "run_id": run_id,
            "source_kind": source_kind,
            "backup2_coverage": backup2_coverage,
            "export_status": export_status,
            "record_count": len(records),
            "targets": target_export_counts,
            "attachment_extraction_complete": False,
        },
    )
    write_json(out / "target-conversations.json", targets)
    return out
