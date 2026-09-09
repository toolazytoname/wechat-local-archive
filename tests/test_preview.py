from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wechat_export.archive_index import build_index
from wechat_export.preview import preview_message


class PreviewTests(unittest.TestCase):
    def test_plain_text_is_readable(self) -> None:
        preview, readable = preview_message("私信班主任了", "text")
        self.assertTrue(readable)
        self.assertEqual(preview, "私信班主任了")

    def test_xml_image_is_not_readable(self) -> None:
        preview, readable = preview_message('<?xml version="1.0"?><msg><img/></msg>', "image")
        self.assertFalse(readable)
        self.assertEqual(preview, "[图片]")

    def test_app_title_extracted(self) -> None:
        xml = '<?xml version="1.0"?><msg><appmsg><title>我用 AI 开发的第三款游戏</title></appmsg></msg>'
        preview, readable = preview_message(xml, "app")
        self.assertFalse(readable)
        self.assertIn("我用 AI 开发的第三款游戏", preview)

    def test_index_and_readable_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            all_dir = root / "all"
            all_dir.mkdir()
            (all_dir / "conversations.jsonl").write_text(
                json.dumps(
                    {
                        "conversation_id": "wxid_apan",
                        "conversation_type": "private",
                        "conversation_display_name": "阿盼仔",
                        "count": 2,
                        "first_timestamp_utc": "2026-01-01T00:00:00+00:00",
                        "last_timestamp_utc": "2026-01-01T00:00:01+00:00",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            lines = [
                {
                    "record_uid": "a",
                    "conversation_id": "wxid_apan",
                    "conversation_type": "private",
                    "sender_id": "me",
                    "sender_display_name": "me",
                    "is_self": True,
                    "timestamp_utc": "2026-01-01T00:00:00+00:00",
                    "message_type_normalized": "text",
                    "text": "hello",
                    "source_kind": "live-db",
                },
                {
                    "record_uid": "b",
                    "conversation_id": "wxid_apan",
                    "conversation_type": "private",
                    "sender_id": "wxid_apan",
                    "sender_display_name": "阿盼仔",
                    "is_self": False,
                    "timestamp_utc": "2026-01-01T00:00:01+00:00",
                    "message_type_normalized": "image",
                    "text": "<?xml version='1.0'?><msg><img/></msg>",
                    "source_kind": "live-db",
                },
            ]
            (all_dir / "messages.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps({"source_kind": "live-db", "backup2_coverage": "unverified", "record_count": 2}),
                encoding="utf-8",
            )
            summary = build_index(root)
            self.assertEqual(summary["message_count"], 2)
            self.assertEqual(summary["readable_count"], 1)
