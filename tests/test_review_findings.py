from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request
from pathlib import Path

from wechat_export.insights.consent import consume_ticket, issue_ticket, require_json_true
from wechat_export.insights.identity import audit_self, save_identity
from wechat_export.insights.profile_pipeline import (
    add_correction,
    collect_profile_records,
    extract_friend_observations,
    run_profile,
)
from wechat_export.insights.providers import RejectRedirectHandler
from wechat_export.insights.store import InsightsError, open_store
from wechat_export.learning.exporter import export_learning_pack
from wechat_export.learning.importer import get_item, import_conversation, paste_body
from wechat_export.learning.notes import save_note
from wechat_export.recovered_media import SQL
from tests.test_insights_identity import _msg, _tree


class ReviewFindingTests(unittest.TestCase):
    def test_r02_conversation_scope_and_friend_dates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="a1", conversation_id="chat_a", text="我想完成计划A。"),
                    _msg(record_uid="a2", conversation_id="chat_a", text="我想再写一次。"),
                    _msg(record_uid="a3", conversation_id="chat_a", text="随便说一句也算样本。"),
                    _msg(record_uid="b1", conversation_id="chat_b", text="我想隐藏在未选择会话的事情。"),
                    _msg(
                        record_uid="f_old",
                        is_self=False,
                        sender_id="friend",
                        conversation_id="chat_a",
                        timestamp_utc="2020-01-01T00:00:00+00:00",
                        text="旧的对方自述",
                    ),
                    _msg(
                        record_uid="f_new",
                        is_self=False,
                        sender_id="friend",
                        conversation_id="chat_a",
                        timestamp_utc="2026-02-01T00:00:00+00:00",
                        text="新的对方自述",
                    ),
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            packed = collect_profile_records(
                conn, kind="self", self_ids=["me"], excluded=set(), scope={"conversation_ids": ["chat_a"]}
            )
            self.assertFalse(any(row["conversation_id"] == "chat_b" for row in packed["records"]))
            with self.assertRaises(InsightsError) as ctx:
                collect_profile_records(conn, kind="self", self_ids=["me"], excluded=set(), scope={"conversation_ids": []})
            self.assertEqual(ctx.exception.code, "empty_scope")
            old = extract_friend_observations(
                conn,
                friend_sender_ids=["friend"],
                excluded=set(),
                scope={"conversation_id": "chat_a", "since": "2026-01-01"},
            )
            blob = " ".join(o["statement"] for o in old)
            self.assertIn("新的对方自述", blob)
            self.assertNotIn("旧的对方自述", blob)
            conn.close()

    def test_r03_forged_source_text_is_not_published(self) -> None:
        class Provider:
            kind = "remote"
            engine_id = "synthetic-adversarial-no-network"

            def analyze(self, payload):
                uid = payload["records"][0]["record_uid"]
                return {
                    "observations": [
                        {
                            "dimension": "plans",
                            "statement": "捏造的事实",
                            "basis": "explicit_fact",
                            "quote": "根本不存在的原话",
                            "source_text": "根本不存在的原话",
                            "evidence_ids": [uid],
                            "evidence": [
                                {
                                    "record_uid": uid,
                                    "sender_id": "me",
                                    "conversation_id": "wrong-chat",
                                    "quote": "根本不存在的原话",
                                }
                            ],
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="s1", text="我想每周整理一次笔记。"),
                    _msg(record_uid="s2", text="我想留下读后感。"),
                    _msg(record_uid="s3", text="随便说一句也算样本。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev",
                scope={},
                subject_person_id="self",
                engine_id="test",
                provider=Provider(),
            )
            conn.close()
            statements = " ".join(o["statement"] for o in run["observations"])
            self.assertNotIn("捏造的事实", statements)
            self.assertNotIn("根本不存在的原话", statements)
            self.assertNotEqual(run["status"], "completed")

    def test_r04_duplicate_plan_does_not_fail_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rows = [
                _msg(record_uid=f"d{i}", text="我想每周看一本书。", timestamp_utc=f"2026-01-0{i+1}T01:00:00+00:00")
                for i in range(3)
            ]
            root = _tree(Path(td) / "a", rows)
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev",
                scope={},
                subject_person_id="self",
                engine_id="local_explicit",
            )
            conn.close()
            self.assertIn(run["status"], {"completed", "insufficient"})
            matches = [o for o in run["observations"] if "每周看一本书" in o["statement"]]
            self.assertEqual(len(matches), 1)
            self.assertGreaterEqual(len(matches[0]["evidence"]), 3)

    def test_r05_excluded_observation_does_not_return(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="s1", text="我想每周整理一次笔记。"),
                    _msg(record_uid="s2", text="我想留下读后感。"),
                    _msg(record_uid="s3", text="随便说一句也算样本。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            first = run_profile(store, conn, kind="self", source_revision="rev", scope={}, subject_person_id="self", engine_id="local_explicit")
            target = next(o for o in first["observations"] if "每周整理一次笔记" in o["statement"])
            add_correction(store, target["observation_id"], "exclude", None)
            again = run_profile(store, conn, kind="self", source_revision="rev", scope={}, subject_person_id="self", engine_id="local_explicit")
            conn.close()
            self.assertFalse(any(o["statement"] == target["statement"] and o["review_state"] == "active" for o in again["observations"]))

    def test_r06_reported_speech_is_not_self_fact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="s1", text="他昨天说：“我想去月球。”但这不是我的计划。"),
                    _msg(record_uid="s2", text="我想每周整理一次笔记。"),
                    _msg(record_uid="s3", text="随便说一句也算样本。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            run = run_profile(store, conn, kind="self", source_revision="rev", scope={}, subject_person_id="self", engine_id="local_explicit")
            conn.close()
            self.assertFalse(any("月球" in o["statement"] for o in run["observations"]))
            self.assertTrue(all(o["basis"] != "explicit_fact" or "月球" not in o["statement"] for o in run["observations"]))

    def test_r07_same_archive_keeps_notes_across_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td) / "a", [_msg(record_uid="1", text="hello")])
            data = Path(td) / "data"
            s1 = open_store(data, "r1", archive_root=root)
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(s1, audit_self(conn))
            from wechat_export.learning.importer import _upsert_item

            item, _ = _upsert_item(s1, kind="saved_text", title="n", canonical_key="k", content_state="has_body")
            save_note(s1, item, "keep-me")
            s1.close()
            s2 = open_store(data, "r2", archive_root=root)
            self.assertEqual(s2.conn.execute("SELECT count(*) FROM identity").fetchone()[0], 1)
            self.assertEqual(s2.conn.execute("SELECT count(*) FROM notes").fetchone()[0], 1)
            other = _tree(Path(td) / "b", [_msg(record_uid="9", text="other")])
            s3 = open_store(data, "r1", archive_root=other)
            self.assertEqual(s3.conn.execute("SELECT count(*) FROM identity").fetchone()[0], 0)
            s2.close()
            s3.close()
            conn.close()

    def test_r08_note_edit_returns_latest_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = open_store(Path(td) / "data", "rev")
            from wechat_export.learning.importer import _upsert_item

            item, _ = _upsert_item(store, kind="saved_text", title="n", canonical_key="k", content_state="has_body")
            save_note(store, item, "first")
            save_note(store, item, "second")
            detail = get_item(store, item)
            self.assertEqual(detail["notes"][0]["user_text"], "second")
            self.assertEqual(len(detail["notes"]), 1)

    def test_r11_same_body_second_item_has_readable_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = open_store(Path(td) / "data", "rev")
            from wechat_export.learning.importer import _upsert_item

            a, _ = _upsert_item(store, kind="link", title="A", canonical_key="url-a", content_state="title_only")
            b, _ = _upsert_item(store, kind="link", title="B", canonical_key="url-b", content_state="title_only")
            body = "同一段正文。\n\n第二段。"
            paste_body(store, a, body)
            paste_body(store, b, body)
            one = get_item(store, a)
            two = get_item(store, b)
            self.assertEqual(two["content_state"], "has_body")
            self.assertTrue(two["contents"])
            self.assertEqual(two["contents"][0]["body"], body)
            self.assertNotEqual(one["contents"][0]["content_id"], two["contents"][0]["content_id"])

    def test_r01_string_false_is_not_approval(self) -> None:
        with self.assertRaises(InsightsError) as ctx:
            require_json_true("false")
        self.assertEqual(ctx.exception.code, "needs_consent")
        require_json_true(True)

    def test_r01_ticket_does_not_travel_across_friends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = open_store(Path(td) / "data", "rev")
            ticket = issue_ticket(
                store,
                kind="friend",
                engine_id="byok-openai",
                endpoint="https://token.weichao.site/v1",
                model="gpt-6-astra",
                scope={"friend_sender_ids": ["alice"], "conversation_id": "alice"},
                record_uids=["f1"],
                source_revision="rev",
                identity_revision=1,
            )
            with self.assertRaises(InsightsError) as ctx:
                consume_ticket(
                    store,
                    ticket["ticket_id"],
                    kind="friend",
                    engine_id="byok-openai",
                    endpoint="https://token.weichao.site/v1",
                    model="gpt-6-astra",
                    scope={"friend_sender_ids": ["bob"], "conversation_id": "bob"},
                    record_uids=["f2"],
                    source_revision="rev",
                    identity_revision=1,
                )
            self.assertEqual(ctx.exception.code, "needs_consent")

    def test_r10_redirect_does_not_keep_authorization(self) -> None:
        handler = RejectRedirectHandler()
        req = urllib.request.Request(
            "https://token.weichao.site/v1/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer sk-test-not-real", "Content-Type": "application/json"},
        )
        with self.assertRaises(InsightsError) as ctx:
            handler.redirect_request(req, None, 302, "Found", {}, "https://other.invalid/steal")
        self.assertEqual(ctx.exception.code, "remote_redirect")

    def test_r09_available_media_enters_library_and_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            png = b"\x89PNG\r\n\x1a\n" + b"synthetic-png-bytes"
            digest = hashlib.sha256(png).hexdigest()
            xml = '<?xml version="1.0"?><msg><img md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/></msg>'
            pdf_xml = '<?xml version="1.0"?><msg><appmsg><title>Doc.pdf</title><type>6</type></appmsg></msg>'
            root = _tree(
                Path(td) / "exp",
                [
                    _msg(
                        record_uid="img1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        conversation_display_name="Studio",
                        is_self=False,
                        sender_id="wxid_alice",
                        text=xml,
                        message_type_normalized="image",
                    ),
                    _msg(
                        record_uid="file1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        conversation_display_name="Studio",
                        is_self=False,
                        sender_id="wxid_alice",
                        text=pdf_xml,
                        message_type_normalized="app",
                        timestamp_utc="2026-01-02T02:01:00+00:00",
                    ),
                    _msg(
                        record_uid="note1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        conversation_display_name="Studio",
                        is_self=True,
                        sender_id="me",
                        text="https://example.invalid/x 这是我自己的备注",
                        timestamp_utc="2026-01-02T02:02:00+00:00",
                    ),
                ],
            )
            media = root / "media" / "objects"
            media.mkdir(parents=True)
            (media / f"{digest}.png").write_bytes(png)
            db = sqlite3.connect(root / "media" / "index.sqlite")
            db.executescript(SQL)
            db.execute(
                "INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "img1",
                    "room@chatroom",
                    "image",
                    "available",
                    f"objects/{digest}.png",
                    "image/png",
                    len(png),
                    digest,
                    "pic.png",
                    "full",
                    "md5",
                    None,
                ),
            )
            db.commit()
            db.close()
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            result = import_conversation(store, conn, "room@chatroom", archive_root=root)
            self.assertGreaterEqual(result["items_created"], 2)
            images = [row for row in store.conn.execute("SELECT * FROM learning_items WHERE kind='image'")]
            self.assertTrue(images)
            self.assertEqual(images[0]["content_state"], "has_attachment")
            comments = [row["saved_comment"] for row in store.conn.execute("SELECT saved_comment FROM item_sources") if row["saved_comment"]]
            self.assertTrue(any("备注" in (c or "") for c in comments))
            dest = Path(td) / "pack"
            pack = export_learning_pack(store, dest, archive_root=root)
            self.assertTrue((dest / "media").is_dir())
            self.assertGreaterEqual(pack["media_count"], 1)
            self.assertTrue(any(dest.joinpath("media").rglob("*.png")))
            conn.close()
