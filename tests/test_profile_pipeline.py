from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.insights.identity import save_identity, set_role, audit_self
from wechat_export.insights.profile_pipeline import run_profile
from wechat_export.insights.profile_validate import validate_observation
from wechat_export.insights.providers import MockProvider, resolve_provider, save_provider_config
from wechat_export.insights.store import InsightsError, open_store
from tests.test_insights_identity import _msg, _tree


class ProfilePipelineTests(unittest.TestCase):
    def test_self_profile_uses_own_speech_and_skips_collection_room(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "e",
                [
                    _msg(record_uid="s1", is_self=True, sender_id="me", text="我想每周整理一次笔记，留下读后感。"),
                    _msg(record_uid="s2", is_self=True, sender_id="me", text="I plan to keep a reading log."),
                    _msg(record_uid="s3", is_self=False, sender_id="wxid_alice", text="我想去火星当国王。"),
                    _msg(
                        record_uid="s4",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        conversation_display_name="Studio",
                        is_self=True,
                        sender_id="me",
                        text="我想把收藏群里的文章当成我的职业能力。",
                    ),
                    _msg(record_uid="s5", is_self=True, sender_id="me", text="随便说一句也算样本。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            set_role(store, "room@chatroom", "read_later")
            run = run_profile(store, conn, kind="self", source_revision="rev", scope={}, subject_person_id="self", engine_id="local_explicit")
            conn.close()
            self.assertIn(run["status"], {"completed", "insufficient"})
            statements = " ".join(o["statement"] for o in run["observations"])
            self.assertIn("每周整理一次笔记", statements)
            self.assertNotIn("火星", statements)
            self.assertNotIn("职业能力", statements)
            self.assertTrue(all(not o["synthetic"] for o in run["observations"]))
            self.assertIn("room@chatroom", run["coverage"]["excluded_conversations"])
            self.assertIn("non_readable_by_kind", run["coverage"])

    def test_rejects_forged_evidence(self) -> None:
        with self.assertRaises(InsightsError) as ctx:
            validate_observation(
                {"statement": "x", "evidence_ids": ["missing"], "evidence": [{"record_uid": "missing", "sender_id": "me"}]},
                allowed_uids={"real"},
                self_ids={"me"},
                subject="self",
            )
        self.assertEqual(ctx.exception.code, "evidence_out_of_scope")
        with self.assertRaises(InsightsError) as ctx:
            validate_observation(
                {"statement": "他的MBTI是INTJ", "evidence_ids": ["real"], "evidence": [{"record_uid": "real", "sender_id": "me"}]},
                allowed_uids={"real"},
                self_ids={"me"},
                subject="self",
            )
        self.assertEqual(ctx.exception.code, "sensitive_rejected")

    def test_mock_forbidden_on_real_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            private.mkdir()
            (private / "analysis-provider.json").write_text('{"kind":"mock"}', encoding="utf-8")
            with self.assertRaises(InsightsError) as ctx:
                resolve_provider(private, allow_synthetic=False)
            self.assertEqual(ctx.exception.code, "mock_forbidden")
            mock = MockProvider()
            with self.assertRaises(InsightsError):
                mock.analyze({"allow_synthetic": False})
            save_provider_config(
                private,
                {
                    "kind": "remote",
                    "engine_id": "weichao_gpt-6-astra",
                    "base_url": "https://token.weichao.site/v1",
                    "model": "gpt-6-astra",
                    "api_key": "sk-test-not-real",
                },
            )
            remote = resolve_provider(private, allow_synthetic=False)
            self.assertEqual(remote.kind, "remote")
            with self.assertRaises(InsightsError) as consent:
                remote.analyze({"records": [{"record_uid": "s1", "text": "x"}]})
            self.assertEqual(consent.exception.code, "needs_consent")

    def test_friend_uses_friend_speech_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "e",
                [
                    _msg(record_uid="f1", is_self=False, sender_id="wxid_alice", text="周末去哪儿，我们提前一天商量一下好吗？"),
                    _msg(record_uid="f2", is_self=True, sender_id="me", text="Alice 很喜欢临时决定。"),
                    _msg(record_uid="f3", is_self=False, sender_id="wxid_alice", text="我想先把书架整理完。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            run = run_profile(
                store,
                conn,
                kind="friend",
                source_revision="rev",
                scope={"friend_sender_ids": ["wxid_alice"], "conversation_id": "wxid_alice"},
                subject_person_id="alice",
                engine_id="local_explicit",
            )
            conn.close()
            blob = " ".join(o["statement"] for o in run["observations"])
            self.assertIn("提前一天商量", blob)
            self.assertNotIn("很喜欢临时决定", blob)
