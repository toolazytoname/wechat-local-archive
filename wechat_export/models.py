from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MessageRecord:
    record_uid: str
    account_id: str
    conversation_id: str
    conversation_type: str
    conversation_display_name: str | None
    sender_id: str | None
    sender_display_name: str | None
    is_self: bool | None
    server_message_id: str | None
    local_message_id: str | None
    timestamp_raw: int | None
    timestamp_unit: str | None
    timestamp_utc: str | None
    display_timezone: str
    message_type_raw: int | None
    message_type_normalized: str
    text: str | None
    quoted_record_id: str | None
    attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    source_kind: str = ""
    source_snapshot_id: str | None = None
    source_relative_path: str | None = None
    source_table: str | None = None
    source_row_id: str | None = None
    source_record_offset: int | None = None
    parser_version: str = ""
    parse_status: str = "ok"
    parse_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
