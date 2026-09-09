from __future__ import annotations

import base64
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from wechat_export import PARSER_VERSION
from wechat_export.content import decode_message_content, normalize_local_type
from wechat_export.preview import classify_payload
from wechat_export.models import MessageRecord
from wechat_export.schema import connect_ro, list_tables, map_contact_schema, map_message_schema, msg_table_name


def _colmap(row: sqlite3.Row) -> dict[str, Any]:
    return {k.lower(): row[k] for k in row.keys()}


def load_contacts(contact_db: Path) -> dict[str, dict[str, Any]]:
    conn = connect_ro(contact_db)
    try:
        mapping = map_contact_schema(conn)
        table = mapping["contact_table"]
        if not table:
            return {}
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            d = _colmap(row)
            username = d.get("username") or d.get("user_name")
            if not username:
                continue
            out[str(username)] = {
                "username": str(username),
                "nick_name": d.get("nick_name") or d.get("nickname"),
                "remark": d.get("remark"),
                "alias": d.get("alias"),
                "local_type": d.get("local_type"),
            }
        # chat rooms
        for t in mapping["tables"]:
            if "room" in t.lower():
                for row in conn.execute(f'SELECT * FROM "{t}"'):
                    d = _colmap(row)
                    username = d.get("username") or d.get("user_name") or d.get("chatroomname")
                    if not username:
                        continue
                    out.setdefault(str(username), {})
                    out[str(username)].update(
                        {
                            "username": str(username),
                            "nick_name": d.get("nick_name") or d.get("nickname") or out[str(username)].get("nick_name"),
                            "is_chatroom": True,
                        }
                    )
        return out
    finally:
        conn.close()


def load_name2id(conn: sqlite3.Connection) -> dict[int, str]:
    tables = list_tables(conn)
    if "Name2Id" not in tables:
        return {}
    cols = [r[1] for r in conn.execute('PRAGMA table_info("Name2Id")')]
    name_col = next((c for c in cols if c.lower() in {"user_name", "username"}), cols[-1])
    mapping: dict[int, str] = {}
    for row in conn.execute(f'SELECT rowid, "{name_col}" FROM Name2Id'):
        mapping[int(row[0])] = str(row[1]) if row[1] is not None else ""
    return mapping


def iter_messages(
    message_db: Path,
    *,
    account_id: str,
    source_kind: str,
    source_snapshot_id: str | None,
    display_timezone: str,
    contacts: dict[str, dict[str, Any]],
    self_usernames: set[str],
    skipped: list[dict[str, Any]] | None = None,
) -> Iterable[MessageRecord]:
    conn = connect_ro(message_db)
    try:
        schema = map_message_schema(conn)
        name2id = load_name2id(conn)
        rel = message_db.name
        skipped = skipped if skipped is not None else []
        for table in schema["message_tables"]:
            username_guess = None
            # reverse map: table may correspond to md5(username)
            for uname in contacts:
                if msg_table_name(uname) == table:
                    username_guess = uname
                    break
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            colset = {c.lower() for c in cols}
            if "message_content" not in colset or "create_time" not in colset:
                skipped.append({"path": rel, "table": table, "status": "skipped_incompatible_schema"})
                continue
            rows = conn.execute(f'SELECT rowid AS _rowid, * FROM "{table}" ORDER BY create_time, local_id').fetchall()
            for row in rows:
                d = _colmap(row)
                sender_id = None
                sid = d.get("real_sender_id")
                if sid is not None and int(sid) in name2id:
                    sender_id = name2id[int(sid)]
                conversation_id = username_guess or table
                conv = contacts.get(conversation_id, {})
                display = conv.get("remark") or conv.get("nick_name") or conversation_id
                conv_type = "room" if str(conversation_id).endswith("@chatroom") or conv.get("is_chatroom") else "private"
                real_type, type_name = normalize_local_type(d.get("local_type"))
                text, raw_b64, notes = decode_message_content(d.get("message_content"), d.get("wcdb_ct_message_content"))
                classified = classify_payload(text, type_name)
                attachments: list[dict[str, Any]] = []
                parse_status = "ok"
                if raw_b64:
                    digest = next((n.split(":", 1)[1] for n in notes if n.startswith("not_utf8_sha256:")), "")
                    if not digest:
                        digest = hashlib.sha256(base64.b64decode(raw_b64)).hexdigest()
                    attachments.append(
                        {
                            "encoding": "base64",
                            "data": raw_b64,
                            "sha256": digest,
                        }
                    )
                    notes.append("raw_bytes_preserved")
                    parse_status = "partial"
                if "zstd_failed" in "".join(notes):
                    parse_status = "partial"
                if not classified["readable"] and classified["media_kind"] == "unknown":
                    parse_status = "partial" if parse_status == "ok" else parse_status
                ts = d.get("create_time")
                ts_utc = None
                unit = None
                if ts is not None:
                    n = int(ts)
                    if n > 10**12:
                        unit = "ms"
                        ts_utc = datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()
                    else:
                        unit = "s"
                        ts_utc = datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
                server_id = d.get("server_id")
                local_id = d.get("local_id")
                uid_src = "|".join(
                    [
                        source_kind,
                        rel,
                        table,
                        str(server_id) if server_id not in (None, 0) else "",
                        str(local_id or ""),
                        str(d.get("_rowid")),
                    ]
                )
                rec = MessageRecord(
                    record_uid=hashlib.sha256(uid_src.encode("utf-8")).hexdigest(),
                    account_id=account_id,
                    conversation_id=conversation_id,
                    conversation_type=conv_type,
                    conversation_display_name=display,
                    sender_id=sender_id,
                    sender_display_name=(
                        (contacts.get(sender_id) or {}).get("remark")
                        or (contacts.get(sender_id) or {}).get("nick_name")
                        or sender_id
                    )
                    if sender_id
                    else None,
                    is_self=(sender_id in self_usernames) if sender_id else None,
                    server_message_id=str(server_id) if server_id not in (None,) else None,
                    local_message_id=str(local_id) if local_id is not None else None,
                    timestamp_raw=int(ts) if ts is not None else None,
                    timestamp_unit=unit,
                    timestamp_utc=ts_utc,
                    display_timezone=display_timezone,
                    message_type_raw=int(d["local_type"]) if d.get("local_type") is not None else None,
                    message_type_normalized=type_name,
                    text=classified["body"] if classified["readable"] else text,
                    quoted_record_id=None,
                    payload_kind=classified["media_kind"],
                    media_title=classified["title"],
                    sender_prefix=classified["sender_prefix"],
                    attachment_refs=attachments,
                    source_kind=source_kind,
                    source_snapshot_id=source_snapshot_id,
                    source_relative_path=rel,
                    source_table=table,
                    source_row_id=str(d.get("_rowid")),
                    parser_version=PARSER_VERSION,
                    parse_status=parse_status,
                    parse_notes=notes,
                )
                yield rec
    finally:
        conn.close()


def find_targets(contacts: dict[str, dict[str, Any]], names: list[str]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {n: [] for n in names}
    fields_of = lambda info: [str(info.get(k) or "") for k in ("username", "nick_name", "remark", "alias")]

    def add(name: str, username: str, info: dict[str, Any]) -> None:
        rec = {"username": username, **{k: info.get(k) for k in ("nick_name", "remark", "alias")}}
        if rec not in found[name]:
            found[name].append(rec)

    for username, info in contacts.items():
        for name in names:
            if name and name in fields_of(info):
                add(name, username, info)
    for username, info in contacts.items():
        blob = " ".join(fields_of(info))
        for name in names:
            if found[name]:
                continue
            if name and name in blob:
                add(name, username, info)
    return found
