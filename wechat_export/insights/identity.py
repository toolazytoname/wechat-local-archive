"""Self-identity audit and user-provided person/conversation context."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from wechat_export.insights.store import InsightStore, InsightsError, utc_now

ALLOWED_PURPOSES = {"daily", "read_later", "work", "excluded"}
ALLOWED_RELATIONSHIPS = {"spouse", "friend", "family", "colleague", "other", None}


def audit_self(conn: sqlite3.Connection) -> dict[str, Any]:
    """Use is_self flags only. Never pick the most talkative sender."""
    rows = conn.execute(
        """
        SELECT sender_id, count(*) AS n
        FROM messages
        WHERE is_self = 1 AND sender_id IS NOT NULL AND trim(sender_id) != ''
        GROUP BY sender_id
        ORDER BY n DESC
        """
    ).fetchall()
    ids = [str(row["sender_id"]) for row in rows]
    unknown = int(
        conn.execute(
            "SELECT count(*) FROM messages WHERE is_self = 1 AND (sender_id IS NULL OR trim(sender_id) = '')"
        ).fetchone()[0]
    )
    total_self = int(conn.execute("SELECT count(*) FROM messages WHERE is_self = 1").fetchone()[0])
    if not ids:
        state = "missing"
    elif len(ids) > 1:
        state = "conflict"
    else:
        state = "consistent"
    return {
        "self_sender_ids": ids[:1] if state == "consistent" else ids,
        "verification_state": state,
        "self_message_count": total_self,
        "unknown_self_sender_count": unknown,
        "candidate_counts": {row["sender_id"]: int(row["n"]) for row in rows},
        "notes": "Identity comes from is_self, not from display names or message volume.",
    }


def load_identity(store: InsightStore) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT * FROM identity WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "self_sender_ids": json.loads(row["self_sender_ids"]),
        "verification_state": row["verification_state"],
        "notes": row["notes"],
        "revision": row["revision"],
        "updated_at": row["updated_at"],
    }


def save_identity(store: InsightStore, audit: dict[str, Any], *, confirmed_ids: list[str] | None = None) -> dict[str, Any]:
    current = load_identity(store)
    revision = 1 if current is None else int(current["revision"]) + 1
    if confirmed_ids is not None:
        if len(confirmed_ids) != 1 or not confirmed_ids[0]:
            raise InsightsError("select exactly one self sender id", "identity_unresolved")
        ids = [confirmed_ids[0]]
        state = "user_confirmed"
    else:
        ids = list(audit.get("self_sender_ids") or [])
        state = audit["verification_state"]
        if state != "consistent":
            raise InsightsError("self identity is unresolved", "identity_unresolved")
    store.conn.execute(
        """
        INSERT OR REPLACE INTO identity(id, self_sender_ids, verification_state, notes, revision, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        """,
        (json.dumps(ids), state, audit.get("notes"), revision, utc_now()),
    )
    store.conn.commit()
    saved = load_identity(store)
    assert saved is not None
    return saved


def list_people(store: InsightStore) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT * FROM people ORDER BY person_id").fetchall()
    return [
        {
            "person_id": row["person_id"],
            "sender_ids": json.loads(row["sender_ids"]),
            "display_aliases": json.loads(row["display_aliases"]),
            "relationship": row["relationship"],
            "source": row["source"],
            "confirmed_at": row["confirmed_at"],
            "revision": row["revision"],
        }
        for row in rows
    ]


def upsert_person(store: InsightStore, payload: dict[str, Any]) -> dict[str, Any]:
    sender_ids = [str(x) for x in (payload.get("sender_ids") or []) if str(x).strip()]
    if not sender_ids:
        raise InsightsError("sender_ids required", "invalid_person")
    relationship = payload.get("relationship")
    if relationship not in ALLOWED_RELATIONSHIPS:
        raise InsightsError("unsupported relationship", "invalid_person")
    aliases = [str(x) for x in (payload.get("display_aliases") or []) if str(x).strip()]
    person_id = str(payload.get("person_id") or f"person_{uuid.uuid4().hex[:12]}")
    existing = store.conn.execute("SELECT revision FROM people WHERE person_id = ?", (person_id,)).fetchone()
    revision = 1 if existing is None else int(existing[0]) + 1
    store.conn.execute(
        """
        INSERT OR REPLACE INTO people(person_id, sender_ids, display_aliases, relationship, source, confirmed_at, revision)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id,
            json.dumps(sender_ids),
            json.dumps(aliases),
            relationship,
            "user_provided",
            utc_now(),
            revision,
        ),
    )
    store.conn.commit()
    return next(p for p in list_people(store) if p["person_id"] == person_id)


def list_roles(store: InsightStore) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT * FROM conversation_roles ORDER BY conversation_id").fetchall()
    return [dict(row) for row in rows]


def set_role(store: InsightStore, conversation_id: str, purpose: str) -> dict[str, Any]:
    if purpose not in ALLOWED_PURPOSES:
        raise InsightsError("unsupported conversation purpose", "invalid_purpose")
    if not conversation_id:
        raise InsightsError("conversation_id required", "invalid_purpose")
    existing = store.conn.execute(
        "SELECT revision FROM conversation_roles WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    revision = 1 if existing is None else int(existing[0]) + 1
    excluded = 1 if purpose in {"read_later", "excluded"} else 0
    store.conn.execute(
        """
        INSERT OR REPLACE INTO conversation_roles(conversation_id, purpose, profile_excluded, revision, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, purpose, excluded, revision, utc_now()),
    )
    store.conn.commit()
    row = store.conn.execute(
        "SELECT * FROM conversation_roles WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return dict(row)


def excluded_conversation_ids(store: InsightStore) -> set[str]:
    rows = store.conn.execute(
        "SELECT conversation_id FROM conversation_roles WHERE profile_excluded = 1"
    ).fetchall()
    return {row[0] for row in rows}


def read_later_conversation_ids(store: InsightStore) -> list[str]:
    rows = store.conn.execute(
        "SELECT conversation_id FROM conversation_roles WHERE purpose = 'read_later'"
    ).fetchall()
    return [row[0] for row in rows]


def ambiguous_display_names(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT display_name, count(*) AS n
        FROM conversations
        WHERE display_name IS NOT NULL AND trim(display_name) != ''
        GROUP BY display_name
        HAVING n > 1
        """
    ).fetchall()
    out = []
    for row in rows:
        convos = conn.execute(
            "SELECT conversation_id, conversation_type, message_count FROM conversations WHERE display_name = ?",
            (row["display_name"],),
        ).fetchall()
        out.append(
            {
                "display_name": row["display_name"],
                "matches": [dict(item) for item in convos],
            }
        )
    return out
