from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.test_insights_identity import _msg, _tree
from wechat_export.insights.consent import consume_ticket, issue_ticket
from wechat_export.insights.identity import audit_self, load_identity, save_identity
from wechat_export.insights.profile_pipeline import (
    collect_profile_records,
    get_run,
    run_profile,
)
from wechat_export.insights.profile_validate import classify_statement_support
from wechat_export.insights.store import InsightsError, open_store
from wechat_export.learning.exporter import export_learning_pack
from wechat_export.learning.importer import get_item, import_conversation, paste_body
from wechat_export.learning.notes import save_note
from wechat_export.recovered_media import SQL


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
)


class _Remote:
    kind = "remote"
    engine_id = "synthetic-adversarial-no-network"

    def __init__(self, statement: str, extra_uids: list[str] | None = None) -> None:
        self.statement = statement
        self.extra_uids = extra_uids or []

    def analyze(self, payload):
        uid = payload["records"][0]["record_uid"]
        return {
            "observations": [
                {
                    "statement": self.statement,
                    "evidence_ids": [uid, *self.extra_uids],
                    "evidence": [
                        {
                            "record_uid": uid,
                            "quote": "fabricated quote",
                            "sender_id": "forged",
                            "conversation_id": "forged",
                        }
                    ],
                }
            ]
        }


def _archive_with_plans(td: Path) -> tuple[Path, sqlite3.Connection]:
    root = _tree(
        td / "archive",
        [
            _msg(record_uid="s1", text="我不打算辞职，“我想辞职”只是转述。"),
            _msg(record_uid="s2", text="我想每周整理一次笔记。"),
            _msg(record_uid="s3", text="我想安排读书。"),
            _msg(record_uid="f1", conversation_id="other", is_self=False, sender_id="bob", text="你好"),
        ],
    )
    conn = sqlite3.connect(root / "archive.sqlite")
    conn.row_factory = sqlite3.Row
    return root, conn


class ReviewR2Tests(unittest.TestCase):
    def test_b01_concurrent_consume_exactly_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            store = open_store(Path(td) / "data", "rev2", archive_root=root)
            save_identity(store, audit_self(conn))
            params = dict(
                kind="friend",
                engine_id="synthetic",
                endpoint="https://example.invalid",
                model="mock",
                scope={"friend_sender_ids": ["bob"]},
                record_uids=["f1"],
                source_revision="rev2",
            )
            ticket = issue_ticket(store, identity_revision=load_identity(store)["revision"], **params)
            store.close()
            barrier = threading.Barrier(2)

            class Cursor:
                def __init__(self, cur):
                    self.cur = cur

                def fetchone(self):
                    row = self.cur.fetchone()
                    barrier.wait(timeout=10)
                    return row

                def __getattr__(self, name):
                    return getattr(self.cur, name)

            class Connection:
                def __init__(self, conn):
                    self.conn = conn

                def execute(self, sql, *args):
                    cursor = self.conn.execute(sql, *args)
                    if str(sql).startswith("SELECT * FROM consent_tickets"):
                        return Cursor(cursor)
                    return cursor

                def __getattr__(self, name):
                    return getattr(self.conn, name)

            def consume(_n):
                opened = open_store(Path(td) / "data", "rev2", archive_root=root)
                opened.conn = Connection(opened.conn)
                try:
                    consume_ticket(
                        opened,
                        ticket["ticket_id"],
                        identity_revision=load_identity(opened)["revision"],
                        **params,
                    )
                    return "accepted"
                except InsightsError as exc:
                    self.assertEqual(exc.code, "needs_consent")
                    return "needs_consent"
                finally:
                    opened.close()

            with ThreadPoolExecutor(2) as pool:
                outcomes = list(pool.map(consume, range(2)))
            self.assertEqual(sorted(outcomes), ["accepted", "needs_consent"])
            check = open_store(Path(td) / "data", "rev2", archive_root=root)
            used = check.conn.execute(
                "SELECT used FROM consent_tickets WHERE ticket_id = ?",
                (ticket["ticket_id"],),
            ).fetchone()[0]
            self.assertEqual(int(used), 1)
            check.close()
            conn.close()

    def test_b01_identity_revision_rejects_old_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            store = open_store(Path(td) / "data", "rev2", archive_root=root)
            save_identity(store, audit_self(conn))
            params = dict(
                kind="friend",
                engine_id="synthetic",
                endpoint="https://example.invalid",
                model="mock",
                scope={"friend_sender_ids": ["bob"]},
                record_uids=["f1"],
                source_revision="rev2",
            )
            ticket = issue_ticket(store, identity_revision=load_identity(store)["revision"], **params)
            save_identity(store, audit_self(conn))
            with self.assertRaises(InsightsError) as ctx:
                consume_ticket(
                    store,
                    ticket["ticket_id"],
                    identity_revision=load_identity(store)["revision"],
                    **params,
                )
            self.assertEqual(ctx.exception.code, "needs_consent")
            used = store.conn.execute(
                "SELECT used FROM consent_tickets WHERE ticket_id = ?",
                (ticket["ticket_id"],),
            ).fetchone()[0]
            self.assertEqual(int(used), 0)
            conn.close()

    def test_b02_negation_and_unknown_uid_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            store = open_store(Path(td) / "data", "rev1", archive_root=root)
            save_identity(store, audit_self(conn))
            mixed = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev1",
                scope={},
                subject_person_id=None,
                engine_id="synthetic",
                provider=_Remote("我想辞职", extra_uids=["nonexistent"]),
            )
            self.assertNotEqual(mixed["status"], "completed")
            self.assertFalse(any(o["statement"] == "我想辞职" for o in mixed["observations"]))
            self.assertGreaterEqual(mixed["result"]["rejected_count"], 1)
            negation = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev1",
                scope={},
                subject_person_id=None,
                engine_id="synthetic",
                provider=_Remote("我想辞职"),
            )
            self.assertFalse(any(o["statement"] == "我想辞职" for o in negation["observations"]))
            self.assertNotEqual(negation["status"], "completed")
            conn.close()

    def test_b02_quoted_denial_is_not_self_plan(self) -> None:
        self.assertEqual(
            classify_statement_support("我想辞职", "我不打算辞职，“我想辞职”只是转述。"),
            "contradicted",
        )
        self.assertEqual(
            classify_statement_support("你计划按周整理阅读笔记。", "我想每周整理一次笔记。"),
            "contradicted",
        )
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            store = open_store(Path(td) / "data", "rev1", archive_root=root)
            save_identity(store, audit_self(conn))

            class Paraphrase:
                kind = "remote"
                engine_id = "synthetic-adversarial-no-network"

                def analyze(self, payload):
                    return {"observations": [{"statement": "你计划按周整理阅读笔记。", "evidence_ids": ["s2"]}]}

            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev1",
                scope={},
                subject_person_id=None,
                engine_id="synthetic",
                provider=Paraphrase(),
            )
            self.assertNotEqual(run["status"], "completed")
            self.assertFalse(any(o["statement"] == "你计划按周整理阅读笔记。" for o in run["observations"]))
            conn.close()

    def test_b03_legacy_revision_namespace_migrates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            data = Path(td) / "legacy"
            old = open_store(data, "rev1")
            save_identity(old, audit_self(conn))
            import_conversation(old, conn, "wxid_alice", archive_root=root)
            item_id = old.conn.execute("SELECT item_id FROM learning_items LIMIT 1").fetchone()[0]
            save_note(old, item_id, "synthetic note")
            paste_body(old, item_id, "旧库里的正文应随迁移可读。")
            old.close()
            upgraded = open_store(data, "rev1", archive_root=root)
            self.assertIsNotNone(load_identity(upgraded))
            self.assertEqual(upgraded.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            migrated = get_item(upgraded, item_id)
            self.assertTrue(migrated["contents"])
            bodies = " ".join(content.get("body") or "" for content in migrated["contents"])
            self.assertIn("旧库里的正文", bodies)
            self.assertEqual(upgraded.get_meta("migration_status"), "migrated")
            again = open_store(data, "rev1", archive_root=root)
            self.assertEqual(again.get_meta("migration_status"), "migrated")
            self.assertEqual(again.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            upgraded.close()
            again.close()
            conn.close()

    def test_b03_conflict_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            data = Path(td) / "both"
            current = open_store(data, "rev1", archive_root=root)
            save_identity(current, audit_self(conn))
            current.close()
            old = open_store(data, "rev1")
            from wechat_export.learning.importer import _upsert_item

            item, _ = _upsert_item(old, kind="saved_text", title="old", canonical_key="old-key", content_state="has_body")
            save_note(old, item, "must stay in conflict copy")
            old.close()
            reopened = open_store(data, "rev1", archive_root=root)
            self.assertEqual(reopened.get_meta("migration_status"), "conflict")
            self.assertIsNotNone(load_identity(reopened))
            self.assertEqual(reopened.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
            conflict = Path(reopened.get_meta("legacy_conflict") or "")
            self.assertTrue(conflict.is_dir())
            reopened.close()
            conn.close()

    def test_b04_reimport_upgrades_attachment_and_pack_links_media(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "archive",
                [
                    _msg(
                        record_uid="img1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        text='<msg><img md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/></msg>',
                        message_type_normalized="image",
                    ),
                    _msg(
                        record_uid="note1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        text="https://example.invalid/article SYNTHETIC_SAVE_COMMENT",
                    ),
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            store = open_store(Path(td) / "data", "rev", archive_root=root)
            import_conversation(store, conn, "room@chatroom", archive_root=root)
            img_id = store.conn.execute("SELECT item_id FROM learning_items WHERE kind='image'").fetchone()[0]
            self.assertEqual(get_item(store, img_id)["content_state"], "attachment_missing")
            digest = hashlib.sha256(PNG).hexdigest()
            media = root / "media"
            (media / "objects").mkdir(parents=True)
            (media / "objects" / f"{digest}.png").write_bytes(PNG)
            db = sqlite3.connect(media / "index.sqlite")
            db.executescript(SQL)
            db.execute(
                "INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("img1", "room@chatroom", "image", "available", f"objects/{digest}.png", "image/png", len(PNG), digest, "pic.png", "full", "md5", None),
            )
            db.commit()
            db.close()
            import_conversation(store, conn, "room@chatroom", archive_root=root)
            detail = get_item(store, img_id, archive_root=root)
            self.assertEqual(detail["content_state"], "has_attachment")
            self.assertTrue(detail["attachments"])
            self.assertEqual(detail["attachments"][0]["status"], "available")
            dest = Path(td) / "pack"
            pack = export_learning_pack(store, dest, archive_root=root)
            self.assertGreaterEqual(pack["media_count"], 1)
            md = (dest / "articles" / f"{img_id}.md").read_text(encoding="utf-8")
            html = (dest / "开始阅读.html").read_text(encoding="utf-8")
            article_html = (dest / "articles" / f"{img_id}.html").read_text(encoding="utf-8")
            self.assertIn("media/", md)
            self.assertIn("media/", html)
            self.assertIn("<img", html)
            self.assertIn("<img", article_html)
            rows = [json.loads(line) for line in (dest / "items.jsonl").read_text().splitlines() if line]
            self.assertTrue(any(source.get("saved_comment") for row in rows for source in row["sources"]))
            self.assertTrue(any("SYNTHETIC_SAVE_COMMENT" in path.read_text(errors="replace") for path in dest.rglob("*") if path.is_file()))
            js = Path("wechat_export/static/learning.js").read_text(encoding="utf-8")
            self.assertIn("/api/media?uid=", js)
            self.assertIn("media-thumb", js)
            conn.close()

    def test_b05_friend_coverage_and_timezone_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            store = open_store(Path(td) / "data", "rev1", archive_root=root)
            save_identity(store, audit_self(conn))
            friend = run_profile(
                store,
                conn,
                kind="friend",
                source_revision="rev1",
                scope={"conversation_id": "other", "friend_sender_ids": ["bob"]},
                subject_person_id=None,
                engine_id="local_explicit",
            )
            self.assertEqual(friend["coverage"]["total_in_scope"], 1)
            self.assertEqual(list(friend["coverage"]["by_conversation"]), ["other"])
            packed = collect_profile_records(
                conn,
                kind="self",
                self_ids=["me"],
                excluded=set(),
                scope={"since": "2026-01-02T09:00:00+08:00"},
            )
            self.assertEqual(len(packed["records"]), 3)
            packed_z = collect_profile_records(
                conn,
                kind="self",
                self_ids=["me"],
                excluded=set(),
                scope={"since": "2026-01-02T01:00:00.000Z"},
            )
            self.assertEqual(len(packed_z["records"]), 3)
            with self.assertRaises(InsightsError) as bad:
                collect_profile_records(conn, kind="self", self_ids=["me"], excluded=set(), scope={"since": "not-a-date"})
            self.assertEqual(bad.exception.code, "invalid_range")
            with self.assertRaises(InsightsError) as inverted:
                collect_profile_records(
                    conn,
                    kind="self",
                    self_ids=["me"],
                    excluded=set(),
                    scope={"since": "2026-02-01", "until": "2026-01-01"},
                )
            self.assertEqual(inverted.exception.code, "invalid_range")
            with self.assertRaises(InsightsError) as unknown:
                collect_profile_records(
                    conn,
                    kind="self",
                    self_ids=["me"],
                    excluded=set(),
                    scope={"conversation_id": "missing-chat"},
                )
            self.assertEqual(unknown.exception.code, "unknown_conversation")
            conn.close()

    def test_b06_old_run_is_stale_after_revision_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, conn = _archive_with_plans(Path(td))
            data = Path(td) / "data"
            store = open_store(data, "rev1", archive_root=root)
            save_identity(store, audit_self(conn))
            from wechat_export.learning.importer import _upsert_item

            item, _ = _upsert_item(store, kind="saved_text", title="n", canonical_key="k", content_state="has_body")
            save_note(store, item, "keep-me")
            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev1",
                scope={},
                subject_person_id=None,
                engine_id="local_explicit",
            )
            store.close()
            later = open_store(data, "rev2", archive_root=root)
            stale = get_run(later, run["run_id"])
            self.assertTrue(stale["stale"])
            self.assertTrue(stale["is_stale"])
            self.assertIn("source_revision_changed", stale["stale_reason"] or "")
            self.assertEqual(stale["source_revision"], "rev1")
            self.assertEqual(later.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            later.close()
            conn.close()

    def test_b07_ui_does_not_claim_profile_complete_or_hide_sample_limits(self) -> None:
        profiles = Path("wechat_export/static/profiles.js").read_text(encoding="utf-8")
        learning = Path("wechat_export/static/learning.js").read_text(encoding="utf-8")
        self.assertIn("不是人格鉴定", profiles)
        self.assertIn("不是画像归纳完成", profiles)
        self.assertIn("观察数不是处理消息数", profiles)
        self.assertIn("/api/insights/exports", profiles)
        self.assertIn("contentStateLabel", learning)
        self.assertNotIn("${item.content_state} ·", learning)
        status = Path("docs/design/profile-learning/implementation-status.md").read_text(encoding="utf-8")
        self.assertIn("旧桌面包已自动更新", status)
        self.assertIn("画像报告导出", status)


if __name__ == "__main__":
    unittest.main()
