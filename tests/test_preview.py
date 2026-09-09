from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wechat_export.archive_index import build_index
from wechat_export.preview import classify_payload, preview_message


class PreviewTests(unittest.TestCase):
    def test_plain_text_is_readable(self) -> None:
        preview, readable = preview_message("hello there", "text")
        self.assertTrue(readable)
        self.assertEqual(preview, "hello there")

    def test_xml_image_is_not_readable(self) -> None:
        preview, readable = preview_message('<?xml version="1.0"?><msg><img/></msg>', "image")
        self.assertFalse(readable)
        self.assertEqual(preview, "[图片]")

    def test_sender_prefixed_xml_is_not_readable(self) -> None:
        payload = "demo_sender:\n<?xml version=\"1.0\"?><msg><img /></msg>"
        info = classify_payload(payload, "text")
        self.assertFalse(info["readable"])
        self.assertEqual(info["sender_prefix"], "demo_sender")
        self.assertEqual(info["media_kind"], "image")
        self.assertEqual(info["preview"], "[图片]")

    def test_prefixed_voice_and_app(self) -> None:
        voice = "wxid_alice:\n<msg><voicemsg voicelength=\"3\"/></msg>"
        info = classify_payload(voice, "text")
        self.assertFalse(info["readable"])
        self.assertEqual(info["media_kind"], "voice")
        app = '<?xml version="1.0"?><msg><appmsg><title>Demo title</title></appmsg></msg>'
        info = classify_payload(app, "app")
        self.assertFalse(info["readable"])
        self.assertIn("Demo title", info["preview"])

    def test_unknown_xml_not_readable(self) -> None:
        info = classify_payload("<msg><unknown/></msg>", "unknown_11000")
        self.assertFalse(info["readable"])
        self.assertEqual(info["media_kind"], "unknown")

    def test_app_title_extracted(self) -> None:
        xml = '<?xml version="1.0"?><msg><appmsg><title>demo game</title></appmsg></msg>'
        preview, readable = preview_message(xml, "app")
        self.assertFalse(readable)
        self.assertIn("demo game", preview)

    def test_index_and_readable_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            all_dir = root / "all"
            all_dir.mkdir()
            (all_dir / "conversations.jsonl").write_text(
                json.dumps(
                    {
                        "conversation_id": "wxid_alice",
                        "conversation_type": "private",
                        "conversation_display_name": "Alice",
                        "count": 3,
                        "first_timestamp_utc": "2026-01-01T00:00:00+00:00",
                        "last_timestamp_utc": "2026-01-01T00:00:02+00:00",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            lines = [
                {
                    "record_uid": "a",
                    "conversation_id": "wxid_alice",
                    "conversation_type": "private",
                    "sender_display_name": "me",
                    "is_self": True,
                    "timestamp_utc": "2026-01-01T00:00:00+00:00",
                    "message_type_normalized": "text",
                    "text": "hello",
                    "source_kind": "live-db",
                },
                {
                    "record_uid": "b",
                    "conversation_id": "wxid_alice",
                    "conversation_type": "private",
                    "sender_display_name": "Alice",
                    "is_self": False,
                    "timestamp_utc": "2026-01-01T00:00:01+00:00",
                    "message_type_normalized": "image",
                    "text": "<?xml version='1.0'?><msg><img/></msg>",
                    "source_kind": "live-db",
                },
                {
                    "record_uid": "c",
                    "conversation_id": "wxid_alice",
                    "conversation_type": "private",
                    "sender_display_name": "Alice",
                    "is_self": False,
                    "timestamp_utc": "2026-01-01T00:00:02+00:00",
                    "message_type_normalized": "text",
                    "text": "wxid_alice:\n<?xml version='1.0'?><msg><img/></msg>",
                    "source_kind": "live-db",
                },
            ]
            (all_dir / "messages.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps({"source_kind": "live-db", "backup2_coverage": "unverified", "record_count": 3}),
                encoding="utf-8",
            )
            summary = build_index(root)
            self.assertEqual(summary["message_count"], 3)
            self.assertEqual(summary["readable_count"], 1)
            self.assertEqual(summary["index_path"].endswith("archive.sqlite"), True)
