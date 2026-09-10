"""Canonical and explanatory sidecars that jointly identify an archive revision."""
SOURCE_FILES = {
    'canonical_sha256': 'all/messages.jsonl',
    'conversations_sha256': 'all/conversations.jsonl',
    'manifest_sha256': 'manifest.json',
    'source_ledger_sha256': 'source-ledger.json',
    'attachment_ledger_sha256': 'attachment-ledger.jsonl',
    'coverage_sha256': 'coverage.json',
    'coverage_markdown_sha256': 'coverage.md',
    'recovered_media_index_sha256': 'media/index.sqlite',
    'recovered_media_report_sha256': 'media/recovery-report.json',
}


def source_paths(root):
    return {key: root / name for key, name in SOURCE_FILES.items()}
