from __future__ import annotations

from collections import Counter
from pathlib import Path

from wechat_export.models import MessageRecord


def build_quality_report(
    records: list[MessageRecord],
    *,
    source_kind: str,
    backup2_coverage: str,
    export_status: str,
    extra_lines: list[str],
) -> str:
    types = Counter(r.message_type_normalized for r in records)
    statuses = Counter(r.parse_status for r in records)
    convos = {r.conversation_id for r in records}
    times = [r.timestamp_utc for r in records if r.timestamp_utc]
    unknown_senders = sum(1 for r in records if not r.sender_id)
    lines = [
        "# quality-report",
        "",
        f"- source_kind: `{source_kind}`",
        f"- backup2_coverage: `{backup2_coverage}`",
        f"- export_status: `{export_status}`",
        f"- record_count: {len(records)}",
        f"- conversation_count: {len(convos)}",
        f"- parse_status: {dict(statuses)}",
        f"- type_distribution: {dict(types)}",
        f"- unknown_sender_count: {unknown_senders}",
        f"- first_timestamp_utc: {min(times) if times else None}",
        f"- last_timestamp_utc: {max(times) if times else None}",
        "",
        "## notes",
    ]
    lines.extend(f"- {x}" for x in extra_lines)
    lines.append("")
    return "\n".join(lines)


def write_quality_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
