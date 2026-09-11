"""Profile quality: grounded summaries, thin-data refusal, offline reveal."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_insights_identity import _msg, _tree
from tests import test_insights_http
from wechat_export.insights.exporter import export_profile
from wechat_export.insights.identity import audit_self, save_identity
from wechat_export.insights.profile_pipeline import run_profile
from wechat_export.insights.profile_validate import (
    classify_observation_support,
    classify_statement_support,
    is_thin_text,
)
from wechat_export.insights.store import open_store


class ProfileQualityTests(unittest.TestCase):
    def test_grounded_summary_keeps_exact_quotes_and_marks_review(self) -> None:
        statement = "近两周多次提到整理阅读笔记。"
        source = "我想每周整理一次阅读笔记，写成短文。"
        self.assertEqual(
            classify_observation_support(
                statement,
                [source],
                quotes=["我想每周整理一次阅读笔记"],
            ),
            "grounded_summary",
        )
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "e",
                [
                    _msg(record_uid="s1", is_self=True, sender_id="me", text=source),
                    _msg(record_uid="s2", is_self=True, sender_id="me", text="我准备先核对原文再写观察。"),
                    _msg(record_uid="s3", is_self=True, sender_id="me", text="我希望示例也能展示中文场景。"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))

            class Summarizer:
                kind = "remote"
                engine_id = "synthetic-summary"

                def analyze(self, payload):
                    return {
                        "observations": [
                            {
                                "dimension": "stated_plans",
                                "statement": statement,
                                "basis": "scoped_observation",
                                "quote": "我想每周整理一次阅读笔记",
                                "evidence": [
                                    {
                                        "record_uid": "s1",
                                        "conversation_id": "wxid_alice",
                                        "sender_id": "me",
                                        "quote": "我想每周整理一次阅读笔记",
                                    }
                                ],
                                "evidence_ids": ["s1"],
                                "caveats": ["待核对"],
                            }
                        ]
                    }

            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev",
                scope={},
                subject_person_id="self",
                engine_id="synthetic",
                provider=Summarizer(),
            )
            conn.close()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["observations"][0]["statement"], statement)
            self.assertEqual(run["observations"][0]["support"], "grounded_summary")
            self.assertEqual(run["observations"][0]["verification"], "citation_checked_meaning_unverified")
            self.assertIn("我想每周整理一次阅读笔记", run["observations"][0]["evidence"][0]["quote"])
            self.assertEqual(run["result"]["grounded_count"], 1)
            dest = Path(td) / "out"
            export_profile(store, run["run_id"], dest)
            html = (dest / "开始阅读.html").read_text(encoding="utf-8")
            self.assertIn("待核对归纳", html)
            self.assertIn(statement, html)

    def test_second_person_restatement_still_rejected(self) -> None:
        self.assertEqual(
            classify_statement_support("你计划按周整理阅读笔记。", "我想每周整理一次笔记。"),
            "contradicted",
        )

    def test_thin_greetings_are_insufficient(self) -> None:
        self.assertTrue(is_thin_text("好的"))
        self.assertTrue(is_thin_text("哈哈哈"))
        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "e",
                [
                    _msg(record_uid="s1", is_self=True, sender_id="me", text="好的"),
                    _msg(record_uid="s2", is_self=True, sender_id="me", text="嗯嗯"),
                    _msg(record_uid="s3", is_self=True, sender_id="me", text="哈哈"),
                    _msg(record_uid="s4", is_self=False, sender_id="wxid_alice", text="在吗"),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))

            class Greeter:
                kind = "remote"
                engine_id = "synthetic-thin"

                def analyze(self, payload):
                    raise AssertionError("thin payloads must not call the model")

            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev",
                scope={},
                subject_person_id="self",
                engine_id="synthetic",
                provider=Greeter(),
            )
            conn.close()
            self.assertEqual(run["status"], "insufficient")
            self.assertEqual(run["result"]["insufficient_reason"], "thin_or_greeting_only")
            self.assertEqual(run["observations"], [])


class RevealOpenHtmlTests(unittest.TestCase):
    def setUp(self):
        self.case = test_insights_http.InsightsHttpTests()
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def test_reveal_can_open_offline_html(self):
        self.case._post("/api/insights/context", {"accept_consistent_self": True})
        status, run = self.case._post(
            "/api/profiles/runs",
            {"kind": "self", "engine": "local_explicit", "scope": {}},
        )
        self.assertEqual(status, 200)
        status, exported = self.case._post(
            "/api/insights/exports",
            {"kind": "profile", "run_id": run["run_id"]},
        )
        self.assertEqual(status, 200)
        calls = []
        with patch("wechat_export.insights_routes.subprocess.run", side_effect=lambda *a, **k: calls.append(a) or None):
            status, result = self.case._post(
                "/api/insights/reveal",
                {"delivery_id": exported["delivery_id"], "open_html": True},
            )
        self.assertEqual(status, 200)
        self.assertEqual(result["opened"], "html")
        self.assertEqual(calls[0][0][0], "open")
        self.assertTrue(str(calls[0][0][1]).endswith("开始阅读.html"))


if __name__ == "__main__":
    unittest.main()
