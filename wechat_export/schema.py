from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1").fetchall()
    return [r[0] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({_ident(table)})")]


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"refusing identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def map_contact_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = list_tables(conn)
    mapping: dict[str, Any] = {"tables": tables, "contact_table": None, "chat_room_table": None, "columns": {}}
    contact_candidates: list[str] = []
    room_candidates: list[str] = []
    for name in tables:
        cols = table_columns(conn, name)
        mapping["columns"][name] = cols
        lower = {c.lower() for c in cols}
        if {"username", "nick_name"} <= lower or {"user_name", "nick_name"} <= lower:
            contact_candidates.append(name)
        if name.lower() in {"chat_room", "chatroom"} or "chat_room" in name.lower() or "chatroom" in name.lower():
            room_candidates.append(name)
    if "contact" in contact_candidates:
        mapping["contact_table"] = "contact"
    elif contact_candidates:
        mapping["contact_table"] = contact_candidates[0]
    if "chat_room" in room_candidates:
        mapping["chat_room_table"] = "chat_room"
    elif room_candidates:
        mapping["chat_room_table"] = room_candidates[0]
    return mapping


def map_session_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = list_tables(conn)
    return {"tables": tables, "columns": {t: table_columns(conn, t) for t in tables}}


def map_message_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = list_tables(conn)
    msg_tables = [t for t in tables if t.startswith("Msg_")]
    name2id = "Name2Id" if "Name2Id" in tables else None
    return {
        "tables": tables,
        "name2id_table": name2id,
        "message_tables": msg_tables,
        "columns": {t: table_columns(conn, t) for t in msg_tables[:5] + ([name2id] if name2id else [])},
    }


def msg_table_name(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
