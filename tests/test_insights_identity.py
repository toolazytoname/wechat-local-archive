from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.archive_index import build_index
from wechat_export.insights.identity import audit_self, save_identity, set_role, upsert_person
from wechat_export.insights.store import InsightsError, open_store


def _tree(root: Path, messages: list[dict]) -> Path:
    all_dir = root / "all"
    all_dir.mkdir(parents=True)
    convos = {}
    for rec in messages:
        convos[rec["conversation_id"]] = {
            "conversation_id": rec["conversation_id"],
            "conversation_type": rec.get("conversation_type", "private"),
            "conversation_display_name": rec.get("conversation_display_name") or rec["conversation_id"],
            "count": convos.get(rec["conversation_id"], {}).get("count", 0) + 1,
            "first_timestamp_utc": rec["timestamp_utc"],
            "last_timestamp_utc": rec["timestamp_utc"],
        }
    (all_dir / "conversations.jsonl").write_text(
        "\n".join(json.dumps(v, ensure_ascii=False) for v in convos.values()) + "\n", encoding="utf-8"
    )
    (all_dir / "messages.jsonl").write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"source_kind": "live-db", "backup2_coverage": "unverified", "record_count": len(messages)}), encoding="utf-8")
    build_index(root)
    return root


def _msg(**kwargs):
    base = {
        "record_uid": "x",
        "conversation_id": "wxid_alice",
        "conversation_type": "private",
        "conversation_display_name": "Alice",
        "sender_id": "me",
        "sender_display_name": "Me",
        "is_self": True,
        "timestamp_utc": "2026-01-02T01:00:00+00:00",
        "message_type_normalized": "text",
        "text": "hello",
        "source_kind": "live-db",
    }
    base.update(kwargs)
    return base


class IdentityAuditTests(unittest.TestCase):
    def test_consistent_self_from_is_self_not_volume(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="1", is_self=True, sender_id="me", text="a"),
                    _msg(record_uid="2", is_self=False, sender_id="wxid_alice", text="b"),
                    _msg(record_uid="3", is_self=False, sender_id="wxid_alice", text="c"),
                    _msg(record_uid="4", is_self=False, sender_id="wxid_alice", text="d"),
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            audit = audit_self(conn)
            conn.close()
            self.assertEqual(audit["verification_state"], "consistent")
            self.assertEqual(audit["self_sender_ids"], ["me"])

    def test_conflict_does_not_pick_busiest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "a",
                [
                    _msg(record_uid="1", is_self=True, sender_id="me"),
                    _msg(record_uid="2", is_self=True, sender_id="other_self"),
                    _msg(record_uid="3", is_self=True, sender_id="other_self"),
                ],
            )
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            audit = audit_self(conn)
            conn.close()
            self.assertEqual(audit["verification_state"], "conflict")
            self.assertEqual(set(audit["self_sender_ids"]), {"me", "other_self"})
            store = open_store(Path(td) / "data", "rev1")
            with self.assertRaises(InsightsError) as ctx:
                save_identity(store, audit)
            self.assertEqual(ctx.exception.code, "identity_unresolved")
            saved = save_identity(store, audit, confirmed_ids=["me"])
            self.assertEqual(saved["self_sender_ids"], ["me"])
            self.assertEqual(saved["verification_state"], "user_confirmed")

    def test_person_and_role_are_user_provided(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = open_store(Path(td), "rev1")
            person = upsert_person(
                store,
                {"sender_ids": ["wxid_alice"], "display_aliases": ["Alice"], "relationship": "friend"},
            )
            self.assertEqual(person["source"], "user_provided")
            role = set_role(store, "room@chatroom", "read_later")
            self.assertEqual(role["purpose"], "read_later")
            self.assertEqual(role["profile_excluded"], 1)
            with self.assertRaises(InsightsError):
                set_role(store, "room@chatroom", "company")
