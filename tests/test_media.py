from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.media_resolve import conversation_hash, resolve_media_file, sniff_media
from wechat_export.offline_html import write_offline_html
from wechat_export.preview import extract_media_meta

JPEG = (
    b"\xff\xd8\xff\xdb\x00C\x00"
    + bytes([8] * 64)
    + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\x3f\xff\xd9"
)


class MediaMetaTests(unittest.TestCase):
    def test_extracts_md5_and_duration(self) -> None:
        xml = 'wxid_alice:\n<?xml version="1.0"?><msg><img md5="C57CB5ABE97F220E63220DEC737D2F37"/></msg>'
        info = extract_media_meta(xml, "text")
        self.assertEqual(info["media_kind"], "image")
        self.assertEqual(info["md5"], "c57cb5abe97f220e63220dec737d2f37")
        voice = '<msg><voicemsg voicelength="3200"/></msg>'
        vinfo = extract_media_meta(voice, "voice")
        self.assertEqual(vinfo["duration_ms"], 3200)


class MediaResolveTests(unittest.TestCase):
    def test_hardlink_and_attach_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conv = "wxid_alice"
            conv_h = conversation_hash(conv)
            md5 = "c57cb5abe97f220e63220dec737d2f37"
            stem = "ca6e9184bdd5d8dce96b4d2b37355164"
            img = root / "msg" / "attach" / conv_h / "2026-01" / "Img" / f"{stem}_t.dat"
            img.parent.mkdir(parents=True)
            img.write_bytes(JPEG)
            db = root / "hardlink.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE image_hardlink_info_v4 (md5 TEXT, file_name TEXT)")
            conn.execute("INSERT INTO image_hardlink_info_v4 VALUES (?, ?)", (md5, stem))
            conn.commit()
            conn.close()
            xml = f'<msg><img md5="{md5}"/></msg>'
            resolved = resolve_media_file(
                media_root=root,
                hardlink_db=db,
                conversation_id=conv,
                timestamp_utc="2026-01-02T01:02:00+00:00",
                payload=xml,
                type_name="image",
            )
            self.assertTrue(resolved["found"])
            self.assertEqual(resolved["mime"], "image/jpeg")
            self.assertEqual(sniff_media(JPEG), "image/jpeg")
            indexed = resolve_media_file(
                media_root=root,
                hardlink_db=db,
                conversation_id=conv,
                timestamp_utc="2026-01-02T01:02:00+00:00",
                payload=None,
                type_name="image",
                md5=md5,
                media_kind="image",
            )
            self.assertTrue(indexed["found"])

    def test_missing_media_is_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            resolved = resolve_media_file(
                media_root=Path(td),
                hardlink_db=None,
                conversation_id="wxid_alice",
                timestamp_utc="2026-01-02T01:02:00+00:00",
                payload="<msg><img md5=\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"/></msg>",
                type_name="image",
            )
            self.assertFalse(resolved["found"])
            self.assertEqual(resolved["status"], "not_found")

    def test_offline_html_escapes_xml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.html"
            write_offline_html(
                path,
                "demo",
                [
                    {
                        "timestamp_utc": "2026-01-02T01:00:00+00:00",
                        "sender_display_name": "Alice",
                        "text": "<script>alert(1)</script>",
                        "readable": True,
                    }
                ],
            )
            html = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertNotIn("<script src=", html)
