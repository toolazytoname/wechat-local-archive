"""Authenticated snapshot -> published canonical archive. Never writes the input."""
from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Callable

from wechat_export.archive_index import build_index
from wechat_export.config import AppConfig
from wechat_export.record_store import RecordStore
from wechat_export.decrypt_livedb import decrypt_one
from wechat_export.export_run import collect_records, export_records
from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.source_ledger import snapshot_inventory, inspect_database_role, LEDGER_VERSION, SCHEMA_VERSION


class PipelineError(RuntimeError):
    pass


class PipelineCancelled(PipelineError):
    pass


def process_snapshot(*, snapshot: Path, passphrase_file: Path, cfg: AppConfig,
                     run_id: str, snapshot_id: str,
                     progress: Callable[[str, int, int], None] = lambda *_: None,
                     cancelled: Callable[[], bool] = lambda: False,
                     output_parent: Path | None = None,
                     validate_destination: Callable[[], None] = lambda: None) -> Path:
    """Unique staging output; publish only after all required DBs/index succeed.

    Only derived FTS failures may be skipped; each is recorded. Every other DB
    failure blocks publication. No unauthenticated plaintext tree is trusted.
    """
    def check():
        if cancelled():
            raise PipelineCancelled('cancelled')

    if not snapshot.is_dir() or snapshot.is_symlink():
        raise PipelineError('snapshot_missing')
    if not passphrase_file.is_file() or passphrase_file.is_symlink():
        raise PipelineError('passphrase_missing')
    if passphrase_file.stat().st_mode & 0o077:
        raise PipelineError('passphrase_permissions')
    with passphrase_file.open("rb") as key_stream:
        secret = key_stream.read(33)
    if len(secret) != 32:
        raise PipelineError('passphrase_invalid')
    if not run_id or Path(run_id).name != run_id or run_id in {'.', '..'}:
        raise PipelineError('invalid_run_id')
    work = cfg.work_root / run_id
    parent = output_parent if output_parent is not None else cfg.exports_root
    if output_parent is None:
        ensure_dir(parent)
    validate_destination()
    from wechat_export.output_locations import directory_identity, check_identity
    parent_identity = directory_identity(parent)
    final = parent / run_id
    if work.exists() or final.exists():
        raise PipelineError('output_exists')
    from wechat_export.storage_plan import estimate_storage
    storage = estimate_storage(source=snapshot, work_root=cfg.data_root, output_root=parent)
    if not storage['estimate_satisfied']:
        raise PipelineError('insufficient_estimated_space')
    ensure_dir(work.parent)
    work.mkdir(mode=0o700, exist_ok=False)
    decrypted = ensure_dir(work / 'decrypted')
    dbs = sorted(snapshot.rglob('*.db'))
    if not (snapshot / 'contact' / 'contact.db').is_file() or not any(
            p != snapshot / 'contact' / 'contact.db' for p in dbs):
        raise PipelineError('required_database_missing')
    results = [{"path": db.relative_to(snapshot).as_posix(), "status": "not_started"} for db in dbs]
    inventory = snapshot_inventory(snapshot, check)
    ledger = {"ledger_version": LEDGER_VERSION, "schema_version": SCHEMA_VERSION,
              "source_snapshot_id": snapshot_id, "source_kind": "live-db", "backup2_coverage": "unverified",
              "snapshot_inventory": inventory, "databases": results, "state": "processing",
              "storage_estimate": storage}
    def save_ledger():
        write_json(work / 'source-ledger.json', ledger)
    save_ledger()
    record_store = None
    publication_stack = ExitStack()
    try:
        for i, db in enumerate(dbs):
            check()
            if db.is_symlink() or not db.resolve().is_relative_to(snapshot.resolve()):
                raise PipelineError('snapshot_symlink')
            progress('decrypting', i, len(dbs))
            for companion in (db, Path(str(db)+'-wal'), Path(str(db)+'-shm')):
                if companion.is_symlink():
                    raise PipelineError('snapshot_symlink')
            rel = db.relative_to(snapshot)
            dst = decrypted / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            rec = results[i]
            rec["status"] = "pending"
            save_ledger()
            try:
                decoded = decrypt_one(db, dst, passphrase=secret, check=check, scratch_parent=cfg.work_root)
                rec.update(decoded)
                rec["path"] = rel.as_posix()
                # A returned result proves codec authentication completed. Exceptions
                # are never downgraded based on a filename or a partial output file.
                if decoded.get('main_pages_hmac') != 'ok':
                    raise PipelineError('source_authentication_unverified')
                role = inspect_database_role(dst)
                rec['schema_evidence'] = role
                if decoded.get('status') != 'ok':
                    if role['role'] != 'derived_fts_only':
                        raise PipelineError('database_integrity_failed')
                    rec['status'] = 'excluded_authenticated_derived_fts'
                    dst.unlink(missing_ok=True)
                else:
                    rec['status'] = 'ok'
            except PipelineCancelled:
                rec['status'] = 'cancelled'
                save_ledger()
                raise
            except Exception as exc:
                rec['status'] = 'failed'
                rec['error_type'] = type(exc).__name__
                save_ledger()
                raise PipelineError('required_database_decrypt_failed') from exc
            save_ledger()
        write_json(work / 'decrypt-report.json', {'results': results, 'source_snapshot_id': snapshot_id})
        check()
        progress('normalizing', 0, 0)
        record_store = RecordStore(work / 'normalized-records.sqlite', check)
        records, targets, detail = collect_records(decrypted, cfg, 'live-db', snapshot_id, record_store=record_store)
        normalized = {item['path']: item for item in detail['databases']}
        for result in results:
            item = normalized.get(result['path'], {})
            result['normalization_status'] = item.get('status', 'excluded' if result['status'].startswith('excluded_') else 'unverified')
            if item.get('skipped_tables'):
                result['skipped_tables'] = item['skipped_tables']
        from wechat_export.source_ledger import database_accounting
        accounting = database_accounting(results, authenticated=True)
        ledger.update(normalization=detail, database_accounting=accounting, output_records=len(records))
        save_ledger()
        if not records:
            raise PipelineError('no_messages')
        if any(r.get('skipped_tables') for r in detail['databases']):
            raise PipelineError('message_tables_unreadable')
        read_paths = {entry['path'] for entry in detail['databases'] if entry['status'] in {'ok','partial'}}
        message_sources = [entry for entry in results if entry.get('schema_evidence',{}).get('role') == 'message_source']
        if any(entry['path'] not in read_paths for entry in message_sources):
            raise PipelineError('message_source_not_exported')
        input_rows = sum(entry['schema_evidence']['input_rows'] for entry in message_sources)
        if input_rows != len(records):
            raise PipelineError('source_record_count_mismatch')
        if snapshot_inventory(snapshot, check) != inventory:
            raise PipelineError('snapshot_changed')
        ledger.update(state='normalized', normalization=detail, input_message_rows=input_rows,
                      output_records=len(records), partial_parse_records=sum(r.parse_status!='ok' for r in records),
                      database_accounting=accounting, recognized_message_tables_complete=True,
                      records_complete=accounting['schema_coverage_complete'],
                      records_complete_scope='selected_snapshot_schema_supported_tables',
                      attachments_complete=False, coverage_verified=False)
        save_ledger()
        check()
        # The staging config keeps partial exports out of the archive registry.
        from wechat_export.archive_publication import ArchivePublication
        validate_destination()
        check_identity(parent_identity)
        publication = publication_stack.enter_context(ArchivePublication(parent, run_id))
        stage_cfg = replace(cfg, data_root=publication.staging_data_root)
        stage = export_records(records, targets, stage_cfg, run_id,
                               source_kind='live-db', backup2_coverage='unverified',
                               extra_notes=['Snapshot pipeline; media availability is not full recovery.'])
        del records
        check()
        progress('indexing', 0, 0)
        manifest = json.loads((stage / 'manifest.json').read_text())
        manifest['source_snapshot_id'] = snapshot_id
        manifest['source_snapshot_sha256'] = inventory['sha256']
        manifest['schema_version'] = SCHEMA_VERSION
        manifest['processing_ledger'] = 'source-ledger.json'
        manifest['records_complete'] = accounting['schema_coverage_complete']
        manifest['recognized_message_tables_complete'] = True
        manifest['database_accounting'] = accounting
        manifest['snapshot_inventory'] = inventory
        if not accounting['schema_coverage_complete']:
            manifest['export_status'] = 'partial'
        manifest['records_complete_scope'] = ledger['records_complete_scope']
        manifest['attachments_complete'] = False
        manifest['coverage_verified'] = False
        manifest['input_message_rows'] = input_rows
        ledger['state'] = 'exported'
        write_json(stage / 'source-ledger.json', ledger)
        manifest['database_results'] = results
        manifest['self_identity_inference'] = detail.get('self_identity_inference')
        manifest['source_account_id'] = cfg.account
        from wechat_export.fsutil import sha256_file
        manifest['processing_ledger_sha256'] = sha256_file(stage / 'source-ledger.json')
        from wechat_export.coverage_report import refresh_full_coverage
        refresh_full_coverage(stage, manifest)
        # Source metadata changed target coverage after the initial writer.
        manifest['generated_files'] = {p.relative_to(stage).as_posix(): sha256_file(p) for p in stage.rglob('*')
                                       if p.is_file() and p != stage / 'manifest.json'}
        write_json(stage / 'manifest.json', manifest)
        build_index(stage)
        check()
        validate_destination()
        publication.publish(stage, check)
        ledger['state'] = 'published'
        save_ledger()
        progress('awaiting_sample_check', manifest['record_count'], manifest['record_count'])
        return final
    except Exception as exc:
        for entry in results:
            if entry['status'] == 'not_started':
                entry['status'] = 'not_processed_due_to_abort'
        ledger['state'] = 'cancelled' if isinstance(exc, PipelineCancelled) else 'failed'
        ledger['records_complete'] = False
        save_ledger()
        write_json(work / 'failure.json', {'error_type': type(exc).__name__, 'database_results': results})
        raise
    finally:
        try:
            if record_store is not None:
                record_store.close()
        finally:
            publication_stack.__exit__(*sys.exc_info())
            del secret
