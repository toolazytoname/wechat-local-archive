"""Consistent selection-vs-source accounting sidecars, emitted before publication."""
from __future__ import annotations
import json
from pathlib import Path
from wechat_export.fsutil import write_json, sha256_file

LINEAGE_FIELDS = ('schema_version', 'parser_version', 'source_snapshot_id', 'source_snapshot_sha256',
                  'records_complete', 'records_complete_scope', 'recognized_message_tables_complete',
                  'attachments_complete', 'coverage_verified', 'database_accounting', 'snapshot_inventory')


def source_lineage(manifest: dict) -> dict:
    return {key: manifest.get(key) for key in LINEAGE_FIELDS}


def write_coverage(root: Path, manifest: dict) -> None:
    source = manifest.get('source_lineage') or source_lineage(manifest)
    value = {'schema': 'wechat-export-coverage/1', 'source_kind': manifest.get('source_kind'),
             'backup2_coverage': manifest.get('backup2_coverage'),
             'selected_record_count': manifest.get('count', manifest.get('record_count')),
             'selected_attachment_accounting': manifest.get('attachment_accounting'),
             'selection_accounting': manifest.get('selection_accounting'),
             'source_lineage': source,
             'scope_note': 'Source database/snapshot accounting describes the input, not only the selected conversations. Null means unverified, not zero.',
             'attachment_note': 'This message export does not copy media binaries. Candidate observations are not content verification or historical recovery.'}
    write_json(root / 'coverage.json', value)
    database = source.get('database_accounting') or {}
    attachment = manifest.get('attachment_accounting') or {}
    selection = manifest.get('selection_accounting') or {}
    lines = ['# 导出范围与完整性', '',
             f"- 消息条数（本次选择）：{value['selected_record_count']}",
             f"- 同会话/时间/类型范围，应用可读内容过滤前：{selection.get('candidate_count', '未验证')}",
             f"- 因可读内容过滤而排除：{selection.get('excluded_unreadable_count', '未验证')}",
             f"- source_kind：{value['source_kind']}", f"- backup2_coverage：{value['backup2_coverage']}",
             f"- 源数据库总数：{database.get('database_count', '未验证')}",
             f"- 已排除的派生数据库：{database.get('excluded_derived_databases', '未验证')}",
             f"- 失败/未处理数据库：{database.get('failed_databases', '未验证')}",
             f"- 未分类表：{database.get('unclassified_table_count', '未验证')}",
             f"- 未分类表行数：{database.get('unclassified_rows', '未验证')}",
             f"- 未分类视图/触发器：{database.get('unclassified_schema_object_count', '未验证')}",
             f"- 跳过的消息表：{database.get('skipped_table_count', '未验证')}",
             f"- 附件引用（本次选择）：{attachment.get('media_references', '未验证')}",
             f"- 已导出附件二进制：{attachment.get('binary_files_exported', 0)}", '',
             '数据库统计对应整个源快照，不应当作本次会话筛选后的数据库统计。',
             '未知表不会被假定为没有消息。已识别消息表导出成功，不等于整个历史完整。',
             '附件引用不等于物理文件；头部识别到候选文件不等于验证或导出该文件。',
             '请将 manifest.json、coverage.json 与 attachment-ledger.jsonl 和消息文件一起保留。', '']
    (root / 'coverage.md').write_text('\n'.join(lines), encoding='utf-8')
    (root / 'coverage.md').chmod(0o600)
    manifest['coverage_report'] = 'coverage.json'
    manifest['coverage_report_sha256'] = sha256_file(root / 'coverage.json')
    if (root / 'attachment-ledger.jsonl').is_file():
        manifest['attachment_ledger_sha256'] = sha256_file(root / 'attachment-ledger.jsonl')


def refresh_full_coverage(root: Path, manifest: dict) -> None:
    for path in (root / 'targets').glob('*/manifest.json'):
        target = json.loads(path.read_text(encoding='utf-8'))
        target['source_lineage'] = source_lineage(manifest)
        write_coverage(path.parent, target)
        write_json(path, target)
    write_coverage(root, manifest)
    write_json(root / 'manifest.json', manifest)
