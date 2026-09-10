"""Build learning items from a designated collection conversation. Offline only."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from typing import Any

from wechat_export.insights.store import InsightStore, InsightsError, utc_now
from wechat_export.learning.canonical import canonical_url_key
from wechat_export.link_urls import safe_web_url
from wechat_export.message_cards import card_details
from wechat_export.preview import record_presentation

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.I)


def _item_id(prefix: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _content_state_for(kind: str, has_body: bool) -> str:
    if has_body:
        return "has_body"
    if kind == "link":
        return "title_only"
    if kind == "file":
        return "attachment_missing"
    if kind == "saved_text":
        return "has_body"
    return "title_only"


def import_conversation(
    store: InsightStore,
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    archive_root: Any | None = None,
) -> dict[str, Any]:
    exists = conn.execute(
        "SELECT conversation_id, display_name FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if exists is None:
        raise InsightsError("conversation not in this archive", "unknown_conversation")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    select = "record_uid, sender_id, is_self, timestamp_utc, message_type, preview, readable, text, media_kind, media_title"
    if "card_json" in cols:
        select += ", card_json"
    rows = conn.execute(
        f"SELECT {select} FROM messages WHERE conversation_id = ? ORDER BY timestamp_utc, record_uid",
        (conversation_id,),
    ).fetchall()
    created = 0
    reused = 0
    sources_added = 0
    for row in rows:
        rec = dict(row)
        presentation = record_presentation({"text": rec.get("text"), "message_type_normalized": rec.get("message_type")})
        card = presentation.get("card")
        if not card or card.get("kind") in {None, "unknown"}:
            if rec.get("card_json"):
                try:
                    card = json.loads(rec["card_json"])
                except json.JSONDecodeError:
                    card = None
            elif rec.get("text"):
                card = card_details(rec["text"] or "")
        if card and card.get("kind") in {None, "unknown"}:
            card = None
        urls: list[str] = []
        text = (rec.get("text") or "").strip()
        if rec.get("readable") and text:
            urls = [u.rstrip(").,，。") for u in URL_IN_TEXT.findall(text)]
        if card and card.get("kind") == "link" and card.get("url"):
            urls.append(card["url"])
        comment = None
        if rec.get("readable") and text:
            leftover = URL_IN_TEXT.sub("", text).strip()
            leftover = leftover.strip(" :-：\n")
            if leftover and leftover != text:
                comment = leftover[:500]
        seen_keys: set[str] = set()
        for raw in urls:
            url = safe_web_url(raw)
            key = canonical_url_key(url)
            if not url or not key or key in seen_keys:
                continue
            seen_keys.add(key)
            title = (card or {}).get("title") or rec.get("media_title") or rec.get("preview") or url
            item_id, is_new = _upsert_item(
                store,
                kind="link",
                title=str(title)[:200],
                canonical_key=key,
                content_state="title_only",
            )
            created += int(is_new)
            reused += int(not is_new)
            sources_added += _add_source(store, item_id, rec, url, comment)
        media_kind = rec.get("media_kind") or (card or {}).get("kind")
        if media_kind in {"image", "video", "file", "voice"} or (card and card.get("kind") == "file"):
            att = None
            if archive_root is not None:
                from wechat_export.recovered_media import attachment

                try:
                    att = attachment(archive_root, rec["record_uid"])
                except (ValueError, FileNotFoundError):
                    att = None
            state = "has_attachment" if att and att.get("status") == "available" else "attachment_missing"
            kind = "file" if media_kind == "file" or (card and card.get("kind") == "file") else str(media_kind or "file")
            title = rec.get("media_title") or rec.get("preview") or (card or {}).get("title") or kind
            key = f"{kind}:{rec['record_uid']}"
            item_id, is_new = _upsert_item(
                store,
                kind=kind,
                title=str(title)[:200],
                canonical_key=key,
                content_state=state,
            )
            created += int(is_new)
            reused += int(not is_new)
            sources_added += _add_source(store, item_id, rec, None, comment)
        if rec.get("readable") and text and not urls and len(text) >= 8:
            key = "text:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            title = text.splitlines()[0][:80]
            item_id, is_new = _upsert_item(
                store,
                kind="saved_text",
                title=title,
                canonical_key=key,
                content_state="has_body",
            )
            created += int(is_new)
            reused += int(not is_new)
            sources_added += _add_source(store, item_id, rec, None, None)
            if is_new:
                _write_body(store, item_id, text, acquisition="chat_text")
    store.conn.commit()
    return {
        "conversation_id": conversation_id,
        "display_name": exists["display_name"],
        "scanned": len(rows),
        "items_created": created,
        "items_reused": reused,
        "sources_added": sources_added,
    }


_CONTENT_RANK = {
    "title_only": 0,
    "attachment_missing": 0,
    "has_attachment": 1,
    "has_body": 2,
}


def _prefer_content_state(current: str, incoming: str) -> str:
    if current == incoming:
        return current
    if current == "has_body" or incoming == "has_body":
        return "has_body"
    return incoming if _CONTENT_RANK.get(incoming, 0) > _CONTENT_RANK.get(current, 0) else current


def _upsert_item(store: InsightStore, *, kind: str, title: str, canonical_key: str, content_state: str) -> tuple[str, bool]:
    row = store.conn.execute(
        "SELECT item_id, revision, content_state FROM learning_items WHERE canonical_key = ?",
        (canonical_key,),
    ).fetchone()
    now = utc_now()
    if row:
        next_state = _prefer_content_state(row["content_state"], content_state)
        if next_state != row["content_state"]:
            store.conn.execute(
                "UPDATE learning_items SET content_state = ?, updated_at = ? WHERE item_id = ?",
                (next_state, now, row["item_id"]),
            )
        return row["item_id"], False
    item_id = _item_id("item", canonical_key)
    store.conn.execute(
        """
        INSERT INTO learning_items(item_id, kind, display_title, reading_state, content_state, topics_json, canonical_key, revision, created_at, updated_at)
        VALUES (?, ?, ?, 'unread', ?, '[]', ?, 1, ?, ?)
        """,
        (item_id, kind, title, content_state, canonical_key, now, now),
    )
    return item_id, True


def _add_source(store: InsightStore, item_id: str, rec: dict[str, Any], url: str | None, comment: str | None) -> int:
    existing = store.conn.execute(
        "SELECT source_id FROM item_sources WHERE item_id = ? AND record_uid = ? AND ifnull(original_url,'') = ifnull(?, '')",
        (item_id, rec["record_uid"], url),
    ).fetchone()
    if existing:
        return 0
    store.conn.execute(
        """
        INSERT INTO item_sources(source_id, item_id, record_uid, saved_at, saved_comment, original_url, fragment_id)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        (f"src_{uuid.uuid4().hex[:12]}", item_id, rec["record_uid"], rec.get("timestamp_utc"), comment, url),
    )
    return 1


def _write_body(store: InsightStore, item_id: str, text: str, *, acquisition: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    version_id = f"c_{item_id}_{digest[:12]}"
    path = store.content_root / f"{digest}.txt"
    if not path.exists():
        path.write_text(text, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    store.conn.execute(
        """
        INSERT OR IGNORE INTO content_versions(content_id, item_id, acquisition, body_hash, available_extent, source_url, acquired_at, paragraph_json, status)
        VALUES (?, ?, ?, ?, 'full', NULL, ?, ?, 'ok')
        """,
        (version_id, item_id, acquisition, digest, utc_now(), json.dumps(paragraphs)),
    )
    store.conn.execute(
        "UPDATE learning_items SET content_state = 'has_body', active_content_id = ?, updated_at = ? WHERE item_id = ?",
        (version_id, utc_now(), item_id),
    )
    return version_id


def list_items(store: InsightStore, *, query: str = "", reading: str = "", content: str = "", topic: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM learning_items WHERE 1=1"
    params: list[Any] = []
    if reading:
        sql += " AND reading_state = ?"
        params.append(reading)
    if content:
        sql += " AND content_state = ?"
        params.append(content)
    if topic:
        sql += " AND topics_json LIKE ?"
        params.append(f"%{topic}%")
    sql += " ORDER BY created_at DESC"
    rows = [dict(r) for r in store.conn.execute(sql, params)]
    out = []
    for row in rows:
        sources = [dict(s) for s in store.conn.execute("SELECT * FROM item_sources WHERE item_id = ? ORDER BY saved_at", (row["item_id"],))]
        notes = store.conn.execute("SELECT count(*) FROM notes WHERE item_id = ?", (row["item_id"],)).fetchone()[0]
        blob = " ".join(
            [
                row["display_title"],
                " ".join(s.get("saved_comment") or "" for s in sources),
            ]
        )
        if query and query.lower() not in blob.lower():
            note_hits = store.conn.execute(
                "SELECT count(*) FROM notes WHERE item_id = ? AND user_text LIKE ?",
                (row["item_id"], f"%{query}%"),
            ).fetchone()[0]
            if not note_hits:
                continue
        out.append(
            {
                **row,
                "topics": json.loads(row["topics_json"] or "[]"),
                "sources": sources,
                "note_count": int(notes),
            }
        )
    return out


_MEDIA_KINDS = {"image", "video", "file", "voice"}


def _attachments_for_sources(
    sources: list[dict[str, Any]],
    archive_root: Any | None,
    *,
    item_kind: str | None = None,
) -> list[dict[str, Any]]:
    if archive_root is None:
        return []
    from wechat_export.recovered_media import attachment, public_attachment

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    media_item = item_kind in _MEDIA_KINDS
    for source in sources:
        uid = source.get("record_uid")
        if not uid or uid in seen:
            continue
        seen.add(str(uid))
        try:
            row = attachment(archive_root, uid)
        except (ValueError, FileNotFoundError):
            row = None
        if row is None:
            if media_item:
                out.append({"status": "missing", "record_uid": uid, "kind": item_kind})
            continue
        info = public_attachment(row)
        info["record_uid"] = uid
        info["kind"] = row.get("kind") or info.get("kind")
        out.append(info)
    return out


def get_item(store: InsightStore, item_id: str, *, archive_root: Any | None = None) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM learning_items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise InsightsError("item not found", "not_found")
    item = dict(row)
    item["topics"] = json.loads(item["topics_json"] or "[]")
    item["sources"] = [dict(s) for s in store.conn.execute("SELECT * FROM item_sources WHERE item_id = ?", (item_id,))]
    contents = [dict(c) for c in store.conn.execute("SELECT * FROM content_versions WHERE item_id = ? ORDER BY acquired_at", (item_id,))]
    for content in contents:
        path = store.content_root / f"{content['body_hash']}.txt" if content.get("body_hash") else store.content_root / f"{content['content_id']}.txt"
        if not path.is_file():
            path = store.content_root / f"{content['content_id']}.txt"
        content["body"] = path.read_text(encoding="utf-8") if path.is_file() else None
        content["paragraphs"] = json.loads(content["paragraph_json"] or "[]")
    active = item.get("active_content_id")
    if active:
        ordered = [row for row in contents if row.get("content_id") != active]
        current = [row for row in contents if row.get("content_id") == active]
        contents = ordered + current
    item["contents"] = contents
    item["notes"] = [dict(n) for n in store.conn.execute("SELECT * FROM notes WHERE item_id = ? ORDER BY updated_at DESC, note_id DESC", (item_id,))]
    item["review_cards"] = [dict(n) for n in store.conn.execute("SELECT * FROM review_cards WHERE item_id = ?", (item_id,))]
    item["summaries"] = [dict(n) for n in store.conn.execute("SELECT * FROM learning_summaries WHERE item_id = ?", (item_id,))]
    item["attachments"] = _attachments_for_sources(item["sources"], archive_root, item_kind=item.get("kind"))
    return item


def patch_item(store: InsightStore, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    current = get_item(store, item_id)
    expected = body.get("revision")
    if expected is not None and int(expected) != int(current["revision"]):
        raise InsightsError("item was updated elsewhere", "revision_conflict")
    reading = body.get("reading_state", current["reading_state"])
    if reading not in {"inbox", "unread", "reading", "read", "archived"}:
        raise InsightsError("invalid reading_state", "invalid_item")
    topics = body.get("topics", current["topics"])
    if not isinstance(topics, list):
        raise InsightsError("topics must be a list", "invalid_item")
    store.conn.execute(
        "UPDATE learning_items SET reading_state = ?, topics_json = ?, revision = ?, updated_at = ? WHERE item_id = ?",
        (reading, json.dumps(topics), int(current["revision"]) + 1, utc_now(), item_id),
    )
    store.conn.commit()
    return get_item(store, item_id)


def paste_body(store: InsightStore, item_id: str, text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise InsightsError("body required", "empty_body")
    get_item(store, item_id)
    _write_body(store, item_id, text.strip(), acquisition="pasted")
    store.conn.commit()
    return get_item(store, item_id)
