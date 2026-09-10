"""Streaming selected-source checks, not a claim of whole-history recovery."""
from __future__ import annotations
import json
from pathlib import Path


def verify_archive(root: Path) -> dict:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    messages = root / 'all/messages.jsonl'
    count = 0
    provenance_ok = True
    identifiers_ok = True
    kinds = set()
    seen_ids = set()
    duplicates = 0
    if messages.is_file():
        with messages.open(encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                count += 1
                if not isinstance(rec, dict):
                    identifiers_ok = False
                    provenance_ok = False
                    continue
                uid = rec.get('record_uid')
                if isinstance(uid, str):
                    if uid in seen_ids:
                        duplicates += 1
                    seen_ids.add(uid)
                kind = rec.get('source_kind')
                if isinstance(kind, str):
                    kinds.add(kind)
                provenance_ok &= kind == manifest.get('source_kind')
                if manifest.get('source_snapshot_id') is not None:
                    provenance_ok &= rec.get('source_snapshot_id') == manifest['source_snapshot_id']
                identifiers_ok &= all(isinstance(rec.get(key), str) and bool(rec[key]) for key in ('record_uid', 'conversation_id'))
    counts_match = isinstance(manifest.get('record_count'), int) and not isinstance(manifest['record_count'], bool) and manifest['record_count'] == count
    coverage_honest = manifest.get('source_kind') == 'live-db' and manifest.get('backup2_coverage') == 'unverified'
    valid = messages.is_file() and counts_match and provenance_ok and identifiers_ok and coverage_honest and duplicates == 0
    return {
        'validation_scope': 'canonical_counts_identifiers_and_provenance_only',
        'duplicate_record_ids': duplicates,
        'archive_valid': bool(valid), 'counts_match': bool(counts_match),
        'manifest_record_count': manifest.get('record_count'), 'jsonl_record_count': count,
        'source_kind': manifest.get('source_kind'), 'source_snapshot_id': manifest.get('source_snapshot_id'),
        'backup2_coverage': manifest.get('backup2_coverage'), 'source_kinds_in_jsonl': sorted(kinds),
        'record_provenance_matches': bool(provenance_ok), 'canonical_identifiers_present': bool(identifiers_ok),
        'messages_file_present': messages.is_file(), 'export_status': manifest.get('export_status'),
        'live_db_text_decode_complete': False,
        'completion_assessment': 'not_proven_by_selected_source_counts',
    }
