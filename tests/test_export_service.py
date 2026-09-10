from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.archive_index import build_index
from wechat_export.export_service import (
    QueryError,
    QuerySpec,
    count_messages,
    create_job_directory,
    csv_safe_cell,
    parse_time_to_ms,
    write_slice,
)


def _demo_tree(root: Path) -> Path:
    all_dir = root / "all"
    all_dir.mkdir(parents=True)
    convos = [
        {
            "conversation_id": "wxid_alice",
            "conversation_type": "private",
            "conversation_display_name": "Alice",
            "count": 3,
            "first_timestamp_utc": "2026-01-02T01:00:00+00:00",
            "last_timestamp_utc": "2026-01-02T01:02:00+00:00",
        },
        {
            "conversation_id": "room@chatroom",
            "conversation_type": "room",
            "conversation_display_name": "Studio",
            "count": 1,
            "first_timestamp_utc": "2026-01-02T02:00:00+00:00",
            "last_timestamp_utc": "2026-01-02T02:00:00+00:00",
        },
    ]
    messages = [
        {
            "record_uid": "1",
            "conversation_id": "wxid_alice",
            "conversation_type": "private",
            "sender_display_name": "Me",
            "is_self": True,
            "timestamp_utc": "2026-01-02T01:00:00+00:00",
            "message_type_normalized": "text",
            "text": "Coffee later?",
            "source_kind": "live-db",
        },
        {
            "record_uid": "2",
            "conversation_id": "wxid_alice",
            "conversation_type": "private",
            "sender_display_name": "Alice",
            "is_self": False,
            "timestamp_utc": "2026-01-02T01:01:00+00:00",
            "message_type_normalized": "text",
            "text": "Yes — the usual place.",
            "source_kind": "live-db",
        },
        {
            "record_uid": "3",
            "conversation_id": "room@chatroom",
            "conversation_type": "room",
            "sender_display_name": "Alice",
            "is_self": False,
            "timestamp_utc": "2026-01-02T02:00:00+00:00",
            "message_type_normalized": "text",
            "text": "Ship the archive UI today.",
            "source_kind": "live-db",
        },
        {
            "record_uid": "4",
            "conversation_id": "wxid_alice",
            "conversation_type": "private",
            "sender_display_name": "Me",
            "is_self": True,
            "timestamp_utc": "2026-01-02T01:02:00+00:00",
            "message_type_normalized": "image",
            "text": "<?xml version='1.0'?><msg><img/></msg>",
            "source_kind": "live-db",
        },
    ]
    (all_dir / "conversations.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in convos) + "\n", encoding="utf-8"
    )
    (all_dir / "messages.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in messages) + "\n", encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({"source_kind": "live-db", "backup2_coverage": "unverified", "record_count": 4}),
        encoding="utf-8",
    )
    build_index(root)
    return root


class TimeParseTests(unittest.TestCase):
    def test_plus_offset_and_z_are_equal(self) -> None:
        a = parse_time_to_ms("2026-01-02T01:00:00+00:00")
        b = parse_time_to_ms("2026-01-02T01:00:00.000Z")
        self.assertEqual(a, b)

    def test_invalid_timezone_rejected(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            QuerySpec.from_mapping({"scope": {"kind": "all"}, "display_timezone": "Not/AZone"})
        self.assertEqual(ctx.exception.code, "invalid_timezone")

    def test_since_after_until_rejected(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            QuerySpec.from_mapping(
                {
                    "scope": {"kind": "all"},
                    "since": "2026-01-03T00:00:00Z",
                    "until": "2026-01-02T00:00:00Z",
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_interval")


class ScopeContractTests(unittest.TestCase):
    def test_empty_ids_are_not_all(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            QuerySpec.from_mapping({"conversation_ids": []})
        self.assertIn(ctx.exception.code, {"scope_required", "empty_selection"})
        with self.assertRaises(QueryError):
            QuerySpec.from_mapping({"scope": {"kind": "conversations", "conversation_ids": []}})

    def test_unknown_ids_rejected(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            QuerySpec.from_mapping(
                {"scope": {"kind": "conversations", "conversation_ids": ["missing"]}},
                known_ids={"wxid_alice"},
            )
        self.assertEqual(ctx.exception.code, "unknown_conversation")

    def test_explicit_all_has_no_ids(self) -> None:
        spec = QuerySpec.from_mapping({"scope": {"kind": "all"}})
        self.assertEqual(spec.scope_kind, "all")
        self.assertEqual(spec.conversation_ids, ())


class SliceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _demo_tree(Path(self.tmp.name) / "demo")
        self.conn = sqlite3.connect(self.root / "archive.sqlite")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_empty_selection_does_not_export_all(self) -> None:
        with self.assertRaises(QueryError):
            QuerySpec.from_mapping({"conversation_ids": []}, known_ids={"wxid_alice", "room@chatroom"})

    def test_explicit_all_exports_four(self) -> None:
        spec = QuerySpec.from_mapping({"scope": {"kind": "all"}, "format": "jsonl"})
        self.assertEqual(count_messages(self.conn, spec), 4)
        result = write_slice(self.conn, self.root, spec)
        lines = Path(result["path"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(result["count"], 4)

    def test_since_zulu_includes_boundary(self) -> None:
        spec = QuerySpec.from_mapping(
            {
                "scope": {"kind": "all"},
                "since": "2026-01-02T01:00:00.000Z",
                "format": "jsonl",
            }
        )
        self.assertEqual(count_messages(self.conn, spec), 4)

    def test_until_is_exclusive(self) -> None:
        spec = QuerySpec.from_mapping(
            {
                "scope": {"kind": "all"},
                "since": "2026-01-02T01:00:00+00:00",
                "until": "2026-01-02T01:01:00.000Z",
                "format": "jsonl",
            }
        )
        self.assertEqual(count_messages(self.conn, spec), 1)

    def test_same_second_jobs_do_not_overwrite(self) -> None:
        spec_all = QuerySpec.from_mapping({"scope": {"kind": "all"}, "format": "jsonl"})
        spec_none = QuerySpec.from_mapping(
            {
                "scope": {"kind": "conversations", "conversation_ids": ["wxid_alice"]},
                "since": "2026-01-03T00:00:00Z",
                "format": "jsonl",
            }
        )
        first = write_slice(self.conn, self.root, spec_all)
        second = write_slice(self.conn, self.root, spec_none)
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(first["count"], 4)
        self.assertEqual(second["count"], 0)
        self.assertTrue(Path(first["path"]).stat().st_size > 0)
        self.assertEqual(len(Path(first["path"]).read_text(encoding="utf-8").splitlines()), 4)

    def test_exclusive_job_dirs(self) -> None:
        parent = self.root / "jobs"
        a = create_job_directory(parent, "fixed-id")
        with self.assertRaises(QueryError):
            create_job_directory(parent, "fixed-id")
        self.assertTrue(a.is_dir())

    def test_csv_formula_is_prefixed(self) -> None:
        self.assertEqual(csv_safe_cell("=1+1"), "'=1+1")
        self.assertEqual(csv_safe_cell("hello"), "hello")

    def test_write_records_can_cancel(self) -> None:
        from wechat_export.export_service import ExportCancelled, write_records

        path = self.root / "cancel.jsonl"
        n = {"i": 0}

        def gen():
            for i in range(20):
                yield {"record_uid": str(i), "text": "x"}

        def should_cancel():
            n["i"] += 1
            return n["i"] > 3

        with self.assertRaises(ExportCancelled):
            write_records(path, gen(), "jsonl", should_cancel=should_cancel)
        self.assertFalse(path.exists())
