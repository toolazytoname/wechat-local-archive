from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.insights.store import InsightsError, open_store
from wechat_export.learning.canonical import canonical_url_key
from wechat_export.learning.exporter import export_learning_pack
from wechat_export.learning.importer import get_item, import_conversation, list_items, paste_body
from wechat_export.learning.notes import add_review_card, save_note
from wechat_export.learning.summaries import summarize_item
from tests.test_insights_identity import _msg, _tree


class CanonicalUrlTests(unittest.TestCase):
    def test_strips_tracking_keeps_article_query(self) -> None:
        a = canonical_url_key("https://Example.COM/p?id=9&utm_source=wechat")
        b = canonical_url_key("https://example.com/p?id=9")
        self.assertEqual(a, b)
        c = canonical_url_key("https://example.com/p?id=10")
        self.assertNotEqual(a, c)


class LearningImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        xml = '<?xml version="1.0"?><msg><appmsg><title>Demo link title</title><url>https://example.invalid/x?utm_campaign=a</url></appmsg></msg>'
        xml2 = '<?xml version="1.0"?><msg><appmsg><title>Demo link title</title><url>https://example.invalid/x</url></appmsg></msg>'
        long_text = "这是一段用户保存的长文本，用来当作学习材料而不是聊天闲聊。" * 3
        self.root = _tree(
            Path(self.tmp.name) / "exp",
            [
                _msg(record_uid="l1", conversation_id="room@chatroom", conversation_type="room", conversation_display_name="Studio", is_self=False, sender_id="wxid_alice", text=xml, message_type_normalized="app"),
                _msg(record_uid="l2", conversation_id="room@chatroom", conversation_type="room", conversation_display_name="Studio", is_self=False, sender_id="wxid_alice", text=xml2, message_type_normalized="app", timestamp_utc="2026-01-02T02:01:00+00:00"),
                _msg(record_uid="l3", conversation_id="room@chatroom", conversation_type="room", conversation_display_name="Studio", is_self=True, sender_id="me", text=long_text, timestamp_utc="2026-01-02T02:02:00+00:00"),
                _msg(record_uid="l4", conversation_id="room@chatroom", conversation_type="room", conversation_display_name="Studio", is_self=False, sender_id="wxid_alice", text="short", timestamp_utc="2026-01-02T02:03:00+00:00"),
            ],
        )
        self.store = open_store(Path(self.tmp.name) / "data", "rev")
        self.conn = sqlite3.connect(self.root / "archive.sqlite")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_duplicate_links_merge_sources(self) -> None:
        summary = import_conversation(self.store, self.conn, "room@chatroom")
        self.assertGreaterEqual(summary["scanned"], 4)
        items = list_items(self.store)
        links = [i for i in items if i["kind"] == "link"]
        self.assertEqual(len(links), 1)
        self.assertEqual(len(links[0]["sources"]), 2)
        self.assertEqual(links[0]["content_state"], "title_only")
        texts = [i for i in items if i["kind"] == "saved_text"]
        self.assertGreaterEqual(len(texts), 1)
        self.assertTrue(any(i["content_state"] == "has_body" for i in texts))

    def test_second_import_does_not_duplicate(self) -> None:
        import_conversation(self.store, self.conn, "room@chatroom")
        again = import_conversation(self.store, self.conn, "room@chatroom")
        self.assertEqual(again["items_created"], 0)
        self.assertEqual(sum(len(i["sources"]) for i in list_items(self.store) if i["kind"] == "link"), 2)

    def test_title_only_cannot_summarize_or_review(self) -> None:
        import_conversation(self.store, self.conn, "room@chatroom")
        link = next(i for i in list_items(self.store) if i["kind"] == "link")
        with self.assertRaises(InsightsError) as ctx:
            summarize_item(self.store, link["item_id"], engine_id="local_excerpt")
        self.assertEqual(ctx.exception.code, "title_only")
        with self.assertRaises(InsightsError):
            add_review_card(self.store, link["item_id"], "q", "a")

    def test_notes_and_pack_survive(self) -> None:
        import_conversation(self.store, self.conn, "room@chatroom")
        text_item = next(i for i in list_items(self.store) if i["kind"] == "saved_text")
        save_note(self.store, text_item["item_id"], "想试着照做一周")
        summarize_item(self.store, text_item["item_id"], engine_id="local_excerpt")
        dest = Path(self.tmp.name) / "pack"
        result = export_learning_pack(self.store, dest)
        self.assertTrue((dest / "开始阅读.html").is_file())
        self.assertTrue((dest / "SHA256SUMS").is_file())
        self.assertNotIn("localhost", (dest / "开始阅读.html").read_text(encoding="utf-8"))
        html = (dest / "开始阅读.html").read_text(encoding="utf-8")
        self.assertNotIn("<script", html)
        self.assertGreater(result["item_count"], 0)
        detail = get_item(self.store, text_item["item_id"])
        self.assertEqual(detail["notes"][0]["user_text"], "想试着照做一周")

    def test_paste_upgrades_title_only(self) -> None:
        import_conversation(self.store, self.conn, "room@chatroom")
        link = next(i for i in list_items(self.store) if i["kind"] == "link")
        paste_body(self.store, link["item_id"], "第一段。\n\n第二段说明方法。")
        item = get_item(self.store, link["item_id"])
        self.assertEqual(item["content_state"], "has_body")
        summarize_item(self.store, link["item_id"], engine_id="local_excerpt")
