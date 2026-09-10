"""Summaries only when a real body exists. No title-only '本文认为'."""

from __future__ import annotations

import json
import uuid
from typing import Any

from wechat_export.insights.store import InsightStore, InsightsError, utc_now
from wechat_export.learning.importer import get_item


def summarize_item(store: InsightStore, item_id: str, *, engine_id: str) -> dict[str, Any]:
    item = get_item(store, item_id)
    if item["content_state"] != "has_body" or not item["contents"]:
        raise InsightsError("没有正文，不能生成文章摘要。", "title_only")
    body = item["contents"][-1].get("body") or ""
    paragraphs = item["contents"][-1].get("paragraphs") or ([body] if body else [])
    if not body.strip():
        raise InsightsError("没有正文，不能生成文章摘要。", "title_only")
    claims = []
    for i, para in enumerate(paragraphs[:5], start=1):
        claims.append({"text": para[:180], "paragraph_id": i, "origin": "unknown", "kind": "excerpt"})
    summary_id = "sum_" + uuid.uuid4().hex[:12]
    store.conn.execute(
        """
        INSERT INTO learning_summaries(summary_id, item_id, content_id, engine_id, claims_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'excerpt', ?)
        """,
        (
            summary_id,
            item_id,
            item["contents"][-1]["content_id"],
            engine_id,
            json.dumps(claims, ensure_ascii=False),
            utc_now(),
        ),
    )
    store.conn.commit()
    return dict(store.conn.execute("SELECT * FROM learning_summaries WHERE summary_id = ?", (summary_id,)).fetchone())
