"""Persistent notes and review cards. Analysis refresh must not overwrite notes."""

from __future__ import annotations

import json
import uuid
from typing import Any

from wechat_export.insights.store import InsightStore, InsightsError, utc_now
from wechat_export.learning.importer import get_item


def save_note(
    store: InsightStore,
    item_id: str,
    text: str,
    *,
    quote_span: str | None = None,
    content_id: str | None = None,
    note_id: str | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    get_item(store, item_id)
    if not text.strip():
        raise InsightsError("note text required", "empty_note")
    existing = None
    if note_id:
        existing = store.conn.execute("SELECT * FROM notes WHERE note_id = ? AND item_id = ?", (note_id, item_id)).fetchone()
        if existing is None:
            raise InsightsError("note not found", "not_found")
    else:
        existing = store.conn.execute(
            "SELECT * FROM notes WHERE item_id = ? ORDER BY updated_at DESC, note_id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
    if existing:
        if revision is not None and int(existing["revision"]) != int(revision):
            raise InsightsError("note was updated elsewhere", "revision_conflict")
        store.conn.execute(
            """
            UPDATE notes SET user_text = ?, quote_span = ?, content_id = ?, revision = ?, updated_at = ?
            WHERE note_id = ?
            """,
            (
                text.strip(),
                quote_span if quote_span is not None else existing["quote_span"],
                content_id if content_id is not None else existing["content_id"],
                int(existing["revision"]) + 1,
                utc_now(),
                existing["note_id"],
            ),
        )
        store.conn.commit()
        row = store.conn.execute("SELECT * FROM notes WHERE note_id = ?", (existing["note_id"],)).fetchone()
        return dict(row)
    new_id = f"note_{uuid.uuid4().hex[:12]}"
    store.conn.execute(
        """
        INSERT INTO notes(note_id, item_id, content_id, quote_span, user_text, revision, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (new_id, item_id, content_id, quote_span, text.strip(), utc_now()),
    )
    store.conn.commit()
    row = store.conn.execute("SELECT * FROM notes WHERE note_id = ?", (new_id,)).fetchone()
    return dict(row)


def add_review_card(store: InsightStore, item_id: str, question: str, answer: str, citations: list[str] | None = None) -> dict[str, Any]:
    item = get_item(store, item_id)
    if item["content_state"] != "has_body":
        raise InsightsError("cannot create review cards without article body", "title_only")
    if not question.strip() or not answer.strip():
        raise InsightsError("question and answer required", "invalid_card")
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    store.conn.execute(
        """
        INSERT INTO review_cards(card_id, item_id, content_id, question, answer, citation_json, user_state)
        VALUES (?, ?, ?, ?, ?, ?, 'new')
        """,
        (card_id, item_id, (item["contents"][-1]["content_id"] if item["contents"] else None), question.strip(), answer.strip(), json.dumps(citations or [])),
    )
    store.conn.commit()
    return dict(store.conn.execute("SELECT * FROM review_cards WHERE card_id = ?", (card_id,)).fetchone())
