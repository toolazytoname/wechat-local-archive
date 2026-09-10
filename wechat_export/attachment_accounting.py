"""Primary-media/reference accounting, never a claim that files were recovered.

No arbitrary attachment path or URL is opened by this module. Payload blobs and
file references are separate categories. Counts describe references, not unique
physical files; nested forwarded payloads/thumbnails are explicitly uninspected.
"""
from __future__ import annotations
import json
from collections import Counter

SCHEMA = 'wechat-attachment-accounting/1'
MEDIA_KINDS = {'image', 'voice', 'video', 'sticker', 'file'}
MAX_REFS = 128
MAX_TEXT = 256000


def attachment_facts(rec: dict, presentation: dict | None = None) -> dict:
    from wechat_export.preview import record_presentation
    text = rec.get('text')
    limited = isinstance(text, str) and len(text) > MAX_TEXT
    if presentation is None:
        view = dict(rec, text=text[:MAX_TEXT]) if limited else rec
        presentation = record_presentation(view)
    kind = presentation.get('media_kind') or 'unknown'
    media = []
    if kind in MEDIA_KINDS:
        media.append({'slot': 'primary', 'kind': kind, 'availability': 'not_checked',
                      'reason': 'binary_not_part_of_message_export', 'binary_included': False,
                      'reference_present': bool(presentation.get('md5'))})
    payloads = unknown = 0
    refs = rec.get('attachment_refs') or []
    if not isinstance(refs, list):
        refs = []; unknown += 1
    for index, ref in enumerate(refs[:MAX_REFS]):
        if not isinstance(ref, dict):
            unknown += 1; continue
        if ref.get('encoding') == 'base64' and isinstance(ref.get('data'), str):
            payloads += 1
        elif ref.get('kind') in MEDIA_KINDS:
            media.append({'slot': f'ref-{index}', 'kind': ref['kind'], 'availability': 'not_checked',
                          'reason': 'declared_reference_not_file_verification', 'binary_included': False,
                          'reference_present': True})
        else:
            unknown += 1
    ignored = max(0, len(refs) - MAX_REFS)
    nested = kind in {'forwarded', 'quote', 'link'}
    structured_unknown = kind in {'app', 'unknown'} and not presentation.get('readable')
    return {'media_refs': media, 'raw_payload_refs': payloads, 'unclassified_refs': unknown,
            'refs_not_inspected': ignored, 'inspection_limited': limited or bool(ignored),
            'nested_media_not_inspected': nested, 'structured_payload_unclassified': structured_unknown}


def attachment_summary(rec: dict, presentation: dict | None = None) -> dict:
    facts = attachment_facts(rec, presentation)
    return {'media_references': len(facts['media_refs']), 'binary_files_exported': 0,
            'availability': 'not_checked' if facts['media_refs'] else 'not_applicable',
            'raw_payload_references': facts['raw_payload_refs'],
            'unclassified_references': facts['unclassified_refs'],
            'inspection_limited': facts['inspection_limited'],
            'nested_media_not_inspected': facts['nested_media_not_inspected']}


def attachment_note(summary: dict | None) -> str:
    summary = summary or {}
    if summary.get('media_references'):
        return f"附件引用 {summary['media_references']} 个；未验证文件可用性，本次未导出附件二进制。"
    if summary.get('nested_media_not_inspected'):
        return '嵌套消息或缩略图中的附件未逐项检查。'
    if summary.get('unclassified_references') or summary.get('inspection_limited'):
        return '存在未分类或未完整检查的附件引用。'
    return ''


class AttachmentAccounting:
    def __init__(self, stream=None, probe=None):
        self.stream = stream
        self.probe = probe
        self.availability = Counter()
        self.counts = Counter()
        self.kinds = Counter()

    def observe(self, rec: dict) -> dict | None:
        facts = attachment_facts(rec)
        self.counts['records_examined'] += 1
        if self.probe:
            facts['media_refs'] = [self.probe.observe(rec, ref) for ref in facts['media_refs']]
        refs = facts['media_refs']
        self.availability.update(ref['availability'] for ref in refs)
        self.counts['records_with_media_references'] += bool(refs)
        self.counts['media_references'] += len(refs)
        self.counts['raw_payload_references'] += facts['raw_payload_refs']
        self.counts['unclassified_references'] += facts['unclassified_refs']
        self.counts['references_not_inspected'] += facts['refs_not_inspected']
        self.counts['inspection_limited_records'] += facts['inspection_limited']
        self.counts['nested_media_uninspected_records'] += facts['nested_media_not_inspected']
        self.counts['unclassified_structured_records'] += facts['structured_payload_unclassified']
        self.kinds.update(ref['kind'] for ref in refs)
        if not (refs or facts['raw_payload_refs'] or facts['unclassified_refs'] or facts['inspection_limited'] or
                facts['nested_media_not_inspected'] or facts['structured_payload_unclassified']):
            return None
        event = {'schema': SCHEMA, 'record_uid': rec.get('record_uid'),
                 'source_kind': rec.get('source_kind'), 'source_snapshot_id': rec.get('source_snapshot_id'),
                 'source_relative_path': rec.get('source_relative_path'), 'source_table': rec.get('source_table'),
                 'source_row_id': rec.get('source_row_id'), **facts}
        self.counts['ledger_records'] += 1
        if self.stream:
            self.stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n')
        return event

    def summary(self) -> dict:
        keys = ('records_examined', 'records_with_media_references', 'media_references', 'raw_payload_references',
                'unclassified_references', 'references_not_inspected', 'inspection_limited_records',
                'nested_media_uninspected_records', 'unclassified_structured_records', 'ledger_records')
        return {'schema': SCHEMA, **{key: self.counts[key] for key in keys},
                'media_kind_counts': dict(sorted(self.kinds.items())),
                'availability_counts': {key: self.availability[key] for key in ('not_checked', 'local_candidate', 'preview_only', 'opaque_candidate', 'not_found_in_supported_layout', 'unsupported', 'inspection_failed')},
                'media_scan_requested': self.probe is not None,
                'media_observation_consistency': 'per_file_stat_and_header_not_snapshot' if self.probe else 'not_scanned',
                'binary_files_exported': 0, 'attachments_complete': False,
                'scope': 'primary_media_and_explicit_references_not_unique_files',
                'exhaustive': False, 'remote_fetch_attempted': False,
                'note': 'Local candidates are header observations, not verified/recovered files. Not-found applies only to the supported local layouts. Raw payload blobs are not recovered media files.'}
