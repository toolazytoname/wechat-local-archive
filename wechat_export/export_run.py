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
    *, record_store=None,
) -> tuple[list[MessageRecord], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    contact_db = decrypted_root / "contact" / "contact.db"
    if not contact_db.exists():
        # allow flat layout
        contact_db = decrypted_root / "contact.db"
    contacts = load_contacts(contact_db) if contact_db.exists() else {}
    names = list(cfg.target_names) if cfg.target_names else list(DEFAULT_TARGET_NAMES)
    targets = find_targets(contacts, names)
    # Never truncate a wxid directory at its first underscore to invent identity.
    self_usernames = {cfg.account_backup_root.name} if not cfg.account_backup_root.name.startswith("acc_") else set()
    records = [] if record_store is None else record_store
    dbs = sorted(decrypted_root.rglob("*.db"))
    from wechat_export.scratch import NAMESPACE
    if any(NAMESPACE in db.relative_to(decrypted_root).parts for db in dbs):
        raise ValueError("unreclaimed_scratch_in_decrypted_tree")
    from wechat_export.source_ledger import inspect_database_role
    db_status = []
    for db in dbs:
        relative_path = db.relative_to(decrypted_root).as_posix()
        role = inspect_database_role(db)
        if role["role"] != "message_source":
            db_status.append({"path": relative_path, "status": "excluded_derived_fts" if role["role"] == "derived_fts_only" else "no_message_tables", "schema_evidence": role})
            continue
        before = len(records)
        skipped: list[dict[str, Any]] = []
        records.extend(
            iter_messages(
                db,
                account_id=cfg.account_backup_root.name,
                source_kind=source_kind,
                source_snapshot_id=source_snapshot_id,
                display_timezone=cfg.display_timezone,
                contacts=contacts,
                self_usernames=self_usernames,
                skipped=skipped,
                source_relative_path=relative_path,
            )
        )
        rec = {"path": relative_path, "status": "ok", "records": len(records) - before, "input_rows": role["input_rows"], "schema_evidence": role}
        if skipped:
            rec["skipped_tables"] = skipped
            rec["status"] = "partial"
        db_status.append(rec)
    inference = None
    if not self_usernames and record_store is not None:
        inference = record_store.infer_self()
    elif not self_usernames:
        peers_by_sender = {}
        for rec in records:
            if rec.conversation_type == "private" and rec.sender_id and rec.sender_id != rec.conversation_id:
                peers_by_sender.setdefault(rec.sender_id, set()).add(rec.conversation_id)
        candidates = [sender for sender, peers in peers_by_sender.items() if len(peers) >= 2]
        if len(candidates) == 1:
            for rec in records:
                rec.is_self = rec.sender_id == candidates[0] if rec.sender_id else None
            inference = "unique_sender_across_multiple_private_peers; inferred, not authenticated identity"
    records.sort(key=sort_key)
    return records, targets, {"contacts": len(contacts), "databases": db_status, "self_identity_inference": inference}


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
    from wechat_export.streaming_export import export_stream, request_digest
    out = cfg.exports_root / run_id
    if out.exists():
        # Preserve the legacy list API's idempotency without overwriting files.
        # Returning an existing output requires identical input/config and all
        # originally generated artifacts to retain their recorded bytes.
        import hashlib, json
        from wechat_export.fsutil import sha256_file
        try:
            manifest = json.loads((out / 'manifest.json').read_text())
            expected = request_digest(targets, cfg, source_kind, backup2_coverage, extra_notes)
            if manifest.get('export_request_sha256') != expected:
                raise ValueError()
            digest = hashlib.sha256()
            for record in records:
                digest.update((json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)+'\n').encode())
            artifacts = manifest.get('generated_files') or {}
            if artifacts.get('all/messages.jsonl') != digest.hexdigest():
                raise ValueError()
            for name, recorded_hash in artifacts.items():
                path = out / name
                if path.is_symlink() or not path.resolve().is_relative_to(out.resolve()) or sha256_file(path) != recorded_hash:
                    raise ValueError()
            return out
        except (OSError, ValueError, TypeError, AttributeError):
            raise FileExistsError('Existing export differs or cannot be verified; choose a new run ID') from None
    return export_stream(records, targets, cfg, run_id, source_kind=source_kind,
                         backup2_coverage=backup2_coverage, extra_notes=extra_notes)
