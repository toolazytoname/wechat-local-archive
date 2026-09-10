from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_insights_identity import _msg, _tree
from wechat_export.insights.identity import audit_self, load_identity, save_identity
from wechat_export.insights.profile_pipeline import collect_profile_records, normalize_scope, run_profile
from wechat_export.insights.profile_validate import classify_statement_support
from wechat_export.insights.store import InsightsError, open_store
from wechat_export.learning.importer import _upsert_item, get_item, import_conversation, paste_body
from wechat_export.learning.notes import save_note


class _Remote:
    kind = "remote"
    engine_id = "synthetic-adversarial-no-network"

    def __init__(self, statement: str) -> None:
        self.statement = statement

    def analyze(self, payload):
        return {"observations": [{"statement": self.statement, "evidence_ids": ["s1"]}]}


class ReviewR3Tests(unittest.TestCase):
    def test_c01_reversals_are_not_supported_restatements(self) -> None:
        pairs = [
            ("我想每周整理一次笔记。", "我从来不整理笔记。", "unsupported"),
            ("我借钱给朋友。", "朋友借钱给我。", "contradicted"),
            ("我不喜欢熬夜。", "你喜欢熬夜。", "contradicted"),
            ("我希望明年去法国旅游。", "我去年已经去法国旅游。", "contradicted"),
            ("我想每周整理一次笔记。", "我想每周整理一次笔记。", "excerpt"),
        ]
        for source, statement, expected in pairs:
            self.assertEqual(classify_statement_support(statement, source), expected, msg=statement)
        with tempfile.TemporaryDirectory() as td:
            for n, (source, statement, expected) in enumerate(pairs[:-1]):
                root = _tree(
                    Path(td) / f"a{n}",
                    [
                        _msg(record_uid="s1", text=source),
                        _msg(record_uid="s2", text="我想看书"),
                        _msg(record_uid="s3", text="我想记笔记"),
                    ],
                )
                conn = sqlite3.connect(root / "archive.sqlite")
                conn.row_factory = sqlite3.Row
                store = open_store(Path(td) / f"d{n}", "rev", archive_root=root)
                save_identity(store, audit_self(conn))
                run = run_profile(
                    store,
                    conn,
                    kind="self",
                    source_revision="rev",
                    scope={},
                    subject_person_id=None,
                    engine_id="synthetic",
                    provider=_Remote(statement),
                )
                self.assertNotEqual(run["status"], "completed", msg=statement)
                self.assertFalse(any(obs["statement"] == statement for obs in run["observations"]), msg=statement)
                conn.close()
                store.close()

    def test_c02_interrupted_migration_retries_instead_of_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "migration-archive"
            root.mkdir()
            data = Path(td) / "migration-data"
            old = open_store(data, "rev1")
            save_identity(old, {"self_sender_ids": ["me"], "verification_state": "consistent"})
            item, _ = _upsert_item(old, kind="link", title="Synthetic article", canonical_key="https://example.invalid/a", content_state="title_only")
            paste_body(old, item, "Synthetic full article body")
            save_note(old, item, "Synthetic note")
            old.close()
            with patch("wechat_export.insights.store._copy_content_files", side_effect=OSError("synthetic disk-full")):
                with self.assertRaises(OSError):
                    open_store(data, "rev1", archive_root=root)
            later = open_store(data, "rev1", archive_root=root)
            record = get_item(later, item)
            self.assertEqual(later.get_meta("migration_status"), "migrated")
            self.assertIsNotNone(load_identity(later))
            self.assertEqual(record["contents"][-1]["body"], "Synthetic full article body")
            self.assertEqual(record["content_state"], "has_body")
            self.assertEqual(len(record["notes"]), 1)
            self.assertGreaterEqual(len(list(later.content_root.glob("*.txt"))), 1)
            later.close()

    def test_c02_changed_revision_requires_legacy_binding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "archive"
            root.mkdir()
            data = Path(td) / "upgrade-after-update"
            old = open_store(data, "older-revision")
            save_identity(old, {"self_sender_ids": ["me"], "verification_state": "consistent"})
            old.close()
            newer = open_store(data, "newer-revision", archive_root=root)
            self.assertIsNone(load_identity(newer))
            self.assertEqual(newer.get_meta("migration_status"), "needs_recovery")
            newer.close()

    def test_c03_link_items_keep_paste_path_and_omit_fake_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "archive",
                [
                    _msg(
                        record_uid="note1",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        text="https://example.invalid/article SYNTHETIC_SAVE_COMMENT",
                    )
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            store = open_store(Path(td) / "data", "rev", archive_root=root)
            import_conversation(store, conn, "room@chatroom", archive_root=root)
            link = next(row for row in store.conn.execute("SELECT item_id FROM learning_items WHERE kind='link'"))
            detail = get_item(store, link["item_id"], archive_root=root)
            self.assertEqual(detail["content_state"], "title_only")
            self.assertFalse(detail["attachments"])
            self.assertTrue(any(source.get("original_url") for source in detail["sources"]))
            js = Path("wechat_export/static/learning.js").read_text(encoding="utf-8")
            self.assertIn("hasAvailableAttachment", js)
            self.assertIn("粘贴已取得的正文", js)
            self.assertIn("hasReadableBody", js)
            conn.close()

    def test_c04_paste_body_a_b_a_returns_a(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = open_store(Path(td) / "data", "rev")
            item, _ = _upsert_item(store, kind="link", title="Synthetic", canonical_key="synthetic-a", content_state="title_only")
            for stamp, body in [
                ("2026-01-01T01:00:00+00:00", "Original article A"),
                ("2026-01-01T02:00:00+00:00", "Edited article B"),
                ("2026-01-01T03:00:00+00:00", "Original article A"),
            ]:
                with patch("wechat_export.learning.importer.utc_now", return_value=stamp):
                    paste_body(store, item, body)
            current = get_item(store, item)
            self.assertEqual(current["contents"][-1]["body"], "Original article A")
            self.assertEqual(len(current["contents"]), 2)
            self.assertEqual(current["active_content_id"], current["contents"][-1]["content_id"])

    def test_c04_http_paste_body_a_b_a_returns_a(self) -> None:
        from tests.test_insights_http import InsightsHttpTests

        case = InsightsHttpTests()
        case.setUp()
        try:
            case._post("/api/insights/context", {"accept_consistent_self": True})
            case._post("/api/learning/imports", {"conversation_id": "room@chatroom"})
            _status, items = case._get("/api/learning/items")
            item = next(row for row in items["items"] if row["kind"] == "link")
            statuses = []
            for stamp, body in [
                ("2026-01-01T01:00:00+00:00", "Article A"),
                ("2026-01-01T02:00:00+00:00", "Article B"),
                ("2026-01-01T03:00:00+00:00", "Article A"),
            ]:
                with patch("wechat_export.learning.importer.utc_now", return_value=stamp):
                    status, _payload = case._post(f"/api/learning/items/{item['item_id']}/content", {"text": body})
                    statuses.append(status)
            status, current = case._get(f"/api/learning/items/{item['item_id']}")
            self.assertEqual(statuses, [200, 200, 200])
            self.assertEqual(status, 200)
            self.assertEqual(current["contents"][-1]["body"], "Article A")
        finally:
            case.tearDown()

    def test_scope_normalize_keeps_subsecond_on_second_call(self) -> None:
        scope = {"since": "2026-01-02T01:00:00.500Z"}
        once = normalize_scope(scope)
        twice = normalize_scope(once)
        self.assertEqual(once["since_ms"], twice["since_ms"])
        self.assertEqual(once["since_ms"], 1767315600500)
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "fractional",
                [
                    _msg(record_uid="early", timestamp_utc="2026-01-02T01:00:00.100+00:00", text="我想早点出发。"),
                    _msg(record_uid="late", timestamp_utc="2026-01-02T01:00:00.900+00:00", text="我想晚点出发。"),
                    _msg(record_uid="s3", timestamp_utc="2026-01-02T02:00:00+00:00", text="我想再写一句。"),
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            direct = collect_profile_records(conn, kind="self", self_ids=["me"], excluded=set(), scope=scope)
            again = collect_profile_records(conn, kind="self", self_ids=["me"], excluded=set(), scope=once)
            self.assertEqual(direct["record_uids"], again["record_uids"])
            self.assertIn("late", direct["record_uids"])
            self.assertNotIn("early", direct["record_uids"])
            with self.assertRaises(InsightsError):
                collect_profile_records(conn, kind="self", self_ids=["me"], excluded=set(), scope={"since": "nope"})
            conn.close()


if __name__ == "__main__":
    unittest.main()
